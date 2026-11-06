/* MPU6050 PS Rev3.4 / RM Rev4.2 acceleration-only digital model.
 * Each read message latches one coherent register frame. Split messages
 * can cross conversion epochs; a valid XYZ output needs all six bytes
 * actually acquired from a common epoch. No mandatory ID-probe sequence.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/mpu6050.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "scenario.h"

struct acquisition {unsigned epoch, vector, mask; uint64_t at;};
static struct acquisition history[64];
static struct bench_i2c bus;
static uint8_t regs[128], pointer;
static unsigned input, frame, epoch, delivered, write_offset;
static uint64_t next_conversion, reset_end;
static bool ready, changing;

static void defaults(void)
{
  memset(regs, 0, sizeof(regs));
  regs[0x6b] = 0x40;
  next_conversion = 0;
  ready = false;
}

static void configuration(void)
{
  assert(!(regs[0x6b] & 0xe0) && (regs[0x6b] & 7) <= 3 && !(regs[0x6c] & 0x38));
  assert((regs[0x1c] & 0xf8) == 0 && (regs[0x1b] & 0xf8) == 0 && regs[0x19] == 9);
  assert((regs[0x1a] & 0x3f) == 0 && regs[0x23] == 0 && (regs[0x6a] & 0x60) == 0);
}

static void tick(uint64_t now)
{
  if (reset_end && now >= reset_end) {reset_end = 0; defaults();}
  if (next_conversion && now >= next_conversion)
    {
      unsigned count = (now - next_conversion) / 1250 + 1;
      next_conversion += (uint64_t)count * 1250;
      epoch += count;
      frame = changing ? (input + epoch - 1) % BENCH_VECTOR_COUNT : input;
      ready = true;
    }
}

static void write_register(unsigned reg, uint8_t value)
{
  assert(reg < 128 && !(reg >= 0x3a && reg <= 0x60) && reg != 0x75);
  if (reset_end) bench_model_unsupported("mpu6050_reset_overlap");
  if (reg == 0x6b && (value & 0x80))
    {
      reset_end = bench_now_us() + 2000;
      next_conversion = 0; ready = false;
      memset(history, 0, sizeof(history));
      return;
    }
  uint8_t old = regs[reg]; regs[reg] = value;
  if (reg == 0x6b)
    {
      if ((old & 0x40) && !(value & 0x40)) {next_conversion = bench_now_us() + 20000; ready = false;}
      if (value & 0x40) {next_conversion = 0; ready = false;}
    }
}

static uint8_t read_register(unsigned reg)
{
  assert(reg < 128);
  if (reg == 0x75) return 0x68; /* Private 0x70 compatibility is not required. */
  if (reg == 0x6b && reset_end) return 0x80;
  if (reg == 0x3a) {uint8_t value = ready && (regs[0x38] & 1); ready = false; return value;}
  if (regs[0x37] & 0x10) ready = false;
  if (reg >= 0x3b && reg <= 0x40)
    {
      configuration();
      unsigned offset = reg - 0x3b;
      if (epoch)
        {
          struct acquisition *a = &history[epoch % 64];
          if (a->epoch != epoch) *a = (struct acquisition){.epoch = epoch, .vector = frame};
          a->mask |= 1u << offset;
          if (a->mask == 63 && !a->at) a->at = bench_now_us();
        }
      return (uint16_t)vectors[frame].raw[offset / 2] >> (offset % 2 ? 0 : 8);
    }
  if (reg >= 0x41 && reg <= 0x48) return (uint8_t)(0xa7 + reg);
  return regs[reg];
}

static int message(void *context, struct i2c_msg_s *msg,
                   bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && !(msg->flags & I2C_M_TEN));
  tick(bench_now_us());
  if (msg->flags & I2C_M_READ)
    {for (unsigned i = 0; i < msg->length; i++) msg->buffer[i] = read_register(pointer++);}
  else
    {
      if (start) write_offset = 0;
      for (unsigned i = 0; i < msg->length; i++)
        {if (write_offset++ == 0) pointer = msg->buffer[i]; else write_register(pointer++, msg->buffer[i]);}
    }
  return 0;
}

static const struct bench_i2c_device device = {.message = message};

static int setup(void)
{
  defaults();
  bench_i2c_init(&bus, &device, NULL, 100000);
  uint64_t begin = bench_now_us();
  assert(mpu6050_register(&bus.dev, BENCH_I2C_ADDRESS, 0) == 0);
  assert(bench_now_us() - begin <= 100000);
  configuration();
  int fd = open("/dev/uorb/sensor_accel0", O_RDONLY);
  assert(fd >= 0);
  return fd;
}

static unsigned sample_observed(int fd, bool permit_failure)
{
  struct sensor_accel event;
  memset(&event, 0xa5, sizeof(event));
  uint64_t begin = bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd, &event, sizeof(event), sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - begin <= 100000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return 0;
    }

  assert(observed_result == sizeof(event));
  assert(bench_now_us() - begin <= 100000 && event.timestamp <= bench_now_us() && isnan(event.temperature));
  float values[] = {event.x, event.y, event.z};
  unsigned match = 0, index = 0;
  for (unsigned i = 0; i < 64; i++)
    {
      struct acquisition *a = &history[i];
      if (a->epoch <= delivered || a->epoch <= match || a->mask != 63 || !a->at || event.timestamp < a->at) continue;
      bool same = true;
      for (unsigned axis = 0; axis < 3; axis++)
        {
          double expected = vectors[a->vector].expected[axis];
          double tolerance = 0.000001 + 2 * FLT_EPSILON * fmax(1, fabs(expected));
          same &= isfinite(values[axis]) && fabs(values[axis] - expected) <= tolerance;
        }
      if (same) {match = a->epoch; index = a->vector;}
    }
  assert(match > delivered);
  delivered = match;
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  printf("BENCHSIM {\"event\":\"sample\",\"epoch\":%u,\"x\":%.8f,\"y\":%.8f,\"z\":%.8f}\n",
         delivered, (double)event.x, (double)event.y, (double)event.z);
  return index;
}
static unsigned sample(int fd)
{
  return sample_observed(fd, false);
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  if (!strcmp(argv[1], "model/coherence_history"))
    {
      regs[0x19] = 9; next_conversion = 1250;
      tick(1250); assert(epoch == 1);
      for (unsigned i = 0; i < 3; i++) read_register(0x3b + i);
      tick(2500); assert(epoch == 2);
      for (unsigned i = 3; i < 6; i++) read_register(0x3b + i);
      assert(history[1].mask == 7 && history[2].mask == 56);
      for (unsigned i = 0; i < 3; i++) read_register(0x3b + i);
      assert(history[2].mask == 63);
      return 0;
    }
  for (unsigned i = 0; i < BENCH_VECTOR_COUNT; i++)
    if (!strcmp(argv[1], vectors[i].id))
      {input = i; int fd = setup(); assert(sample(fd) == i); assert(close(fd) == 0); return 0;}
  int fd = setup();
  if (!strcmp(argv[1], "data/coherence"))
    {changing = true; for (unsigned i = 0; i < 20; i++) sample(fd);}
  else if (!strcmp(argv[1], "data/stable_change") || !strcmp(argv[1], "os/lifecycle"))
    {
      sample(fd); sample(fd); input = BENCH_VECTOR_COUNT - 1;
      bool fresh = false;
      for (unsigned i = 0; i < 3 && !fresh; i++) fresh = sample(fd) == input;
      assert(fresh);
      if (!strcmp(argv[1], "os/lifecycle"))
        {
          char small[1]; assert(read(fd, small, sizeof(small)) < 0);
          assert(close(fd) == 0);
          unsigned before = bus.messages;
          bench_sensor_check_disabled(SENSOR_TYPE_ACCELEROMETER, 0, sizeof(struct sensor_accel));
          assert(bus.messages == before);
          fd = open("/dev/uorb/sensor_accel0", O_RDONLY); assert(fd >= 0); sample(fd);
        }
    }
  else if (!strcmp(argv[1], "integrity/transport_error"))
    {
      bus.next_transfer_error = -EIO;
      sample_observed(fd, true);
      bench_finish_transient(&bus.next_transfer_error);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
    }
  else return 65;
  assert(close(fd) == 0);
  return 0;
}
