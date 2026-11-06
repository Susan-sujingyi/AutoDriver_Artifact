/* FN8371 Rev 3.00, register set and 4000 lux / eight-bit continuous ALS.
 * No fabricated ready bit or required ID-probe sequence. Unused upper ADC
 * bits are nonzero so treating the selected result as 16-bit is observable.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/isl29035.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "scenario.h"

static struct bench_i2c bus;
static uint8_t regs[16], pointer, stimulus, latched;
static unsigned write_offset, epoch, acquired_epoch, delivered, acquisitions;
static uint64_t next_conversion, acquired_at;

static bool configured(void)
{
  return (regs[0] & 0xe3) == 0xa0 && (regs[1] & 15) == 9 && !(regs[15] & 0x80);
}

static void tick(uint64_t now)
{
  if (configured() && next_conversion && now >= next_conversion)
    {
      unsigned count = (now - next_conversion) / 410 + 1;
      epoch += count;
      next_conversion += (uint64_t)count * 410;
      latched = stimulus;
    }
}

static void write_register(unsigned reg, uint8_t value)
{
  assert(reg < 16 && reg != 2 && reg != 3);
  tick(bench_now_us());
  if (reg == 15) regs[15] = (regs[15] & 0x7f) | (value & 0x80);
  else if (regs[reg] != value)
    {
      regs[reg] = value;
      if (reg == 0 || reg == 1)
        next_conversion = configured() ? bench_now_us() + 410 : 0;
    }
}

static uint8_t read_register(unsigned reg)
{
  assert(reg < 16);
  if (reg == 2)
    {
      assert(configured());
      if (epoch)
        {
          acquired_epoch = epoch;
          acquired_at = bench_now_us();
          acquisitions++;
        }
      return latched;
    }
  if (reg == 3) return 0xd3;
  if (reg == 0)
    {
      uint8_t value = regs[0];
      regs[0] &= ~4;
      return value;
    }
  return regs[reg];
}

static int message(void *context, struct i2c_msg_s *msg,
                   bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && !(msg->flags & I2C_M_TEN));
  tick(bench_now_us());
  if (msg->flags & I2C_M_READ)
    {
      for (unsigned i = 0; i < msg->length; i++) msg->buffer[i] = read_register(pointer++);
    }
  else
    {
      if (start) write_offset = 0;
      for (unsigned i = 0; i < msg->length; i++)
        {
          if (write_offset++ == 0) pointer = msg->buffer[i];
          else write_register(pointer++, msg->buffer[i]);
        }
    }
  return 0;
}

static const struct bench_i2c_device device = {.message = message};

static int setup(void)
{
  regs[15] = 0xa8;
  bench_i2c_init(&bus, &device, NULL, 100000);
  uint64_t begin = bench_now_us();
  assert(isl29035_register(&bus.dev, BENCH_I2C_ADDRESS, 0) == 0);
  assert(bench_now_us() - begin <= 100000 && configured());
  int fd = open("/dev/uorb/sensor_light0", O_RDONLY);
  assert(fd >= 0);
  return fd;
}

static void sample_observed(int fd, unsigned index, bool permit_failure)
{
  struct sensor_light event;
  stimulus = vectors[index].raw;
  uint64_t begin = bench_now_us();
  unsigned before = acquisitions;
  memset(&event, 0xa5, sizeof(event));
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd, &event, sizeof(event), sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - begin <= 10000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return;
    }

  assert(observed_result == sizeof(event));
  assert(bench_now_us() - begin <= 10000);
  assert(acquisitions > before && acquired_epoch > delivered);
  assert(isfinite(event.light) && event.light >= vectors[index].low &&
         event.light <= vectors[index].high && isnan(event.ir));
  assert(event.timestamp >= acquired_at && event.timestamp <= bench_now_us());
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  delivered = acquired_epoch;
  printf("BENCHSIM {\"event\":\"sample\",\"epoch\":%u,\"lux\":%.7f}\n", delivered, (double)event.light);
}
static void sample(int fd, unsigned index)
{
  sample_observed(fd, index, false);
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  if (!strcmp(argv[1], "model/conversion"))
    {
      regs[0] = 0xa0; regs[1] = 9; regs[15] = 0x28;
      next_conversion = 410; stimulus = 7;
      tick(409); assert(epoch == 0);
      tick(410); assert(epoch == 1 && latched == 7);
      stimulus = 19;
      tick(819); assert(epoch == 1 && latched == 7);
      tick(1230); assert(epoch == 3 && latched == 19);
      assert(read_register(3) == 0xd3 && acquisitions == 0);
      assert(read_register(2) == 19 && acquisitions == 1 && acquired_epoch == 3);
      return 0;
    }
  for (unsigned i = 0; i < BENCH_VECTOR_COUNT; i++)
    if (!strcmp(argv[1], vectors[i].id))
      {
        stimulus = vectors[i].raw;
        int fd = setup();
        sample(fd, i);
        assert(close(fd) == 0);
        return 0;
      }
  int fd = setup();
  if (!strcmp(argv[1], "data/stable_change") || !strcmp(argv[1], "os/lifecycle"))
    {
      sample(fd, 0); sample(fd, 1); sample(fd, BENCH_VECTOR_COUNT - 1);
      if (!strcmp(argv[1], "os/lifecycle"))
        {
          char small[1];
          assert(read(fd, small, sizeof(small)) < 0);
          assert(close(fd) == 0);
          unsigned before = bus.messages;
          bench_sensor_check_disabled(SENSOR_TYPE_LIGHT, 0, sizeof(struct sensor_light));
          assert(before == bus.messages);
          fd = open("/dev/uorb/sensor_light0", O_RDONLY);
          assert(fd >= 0);
          sample(fd, 0);
        }
    }
  else if (!strcmp(argv[1], "integrity/transport_error"))
    {
      bus.next_transfer_error = -EIO;
      sample_observed(fd, 0, true);
      bench_finish_transient(&bus.next_transfer_error);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
    }
  else return 65;
  assert(close(fd) == 0);
  return 0;
}
