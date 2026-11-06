/* ROHM BH1750FVI No.11046EDT01 Rev.D, command/state/measurement sections.
 * Public NuttX task requires a fresh one-time H-resolution sample per fetch;
 * no optional reset/power-on/explicit MTreg sequence is required.
 * This model owns device state only, never OS allocation/locks/sleep/Sensor.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/bh1750.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "scenario.h"

static struct bench_i2c bus;
static uint8_t mtreg = 69;
static bool powered, converting, valid, inject_read_error, read_error_hit;
static uint64_t ready_at, sample_time;
static uint16_t stimulus, latched;
static unsigned conversions, acquisitions, position;
static unsigned acquired_epoch;

static void tick(uint64_t now)
{
  if (converting && now >= ready_at)
    {
      latched = stimulus;
      converting = false;
      valid = true;
      powered = false;
      sample_time = ready_at;
    }
}

static void command(uint8_t value, uint64_t now)
{
  tick(now);
  switch (value)
    {
      case 0: powered = false; converting = false; break;
      case 1: powered = true; break;
      case 7:
        assert(powered); /* Datasheet: reset is not accepted in power down. */
        valid = false;
        break;
      case 0x20:
        assert(mtreg == 69);
        if (converting) bench_model_unsupported("bh1750_overlapping_conversion");
        converting = powered = true;
        ready_at = now + 180000;
        conversions++;
        break;
      default:
        if ((value >= 0x40 && value <= 0x47) || (value >= 0x60 && value <= 0x7f))
          {
            if (converting) bench_model_unsupported("bh1750_mtreg_during_conversion");
            mtreg = (value & 0xe0) == 0x40 ? (mtreg & 31) | ((value & 7) << 5) :
                                           (mtreg & 0xe0) | (value & 31);
          }
        else
          assert(!"BH1750 task requires one-time high-resolution documented commands");
    }
}

static int message(void *context, struct i2c_msg_s *msg,
                   bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && !(msg->flags & I2C_M_TEN));
  tick(bench_now_us());
  if (!(msg->flags & I2C_M_READ))
    {
      assert(start && stop && msg->length == 1); /* One command then STOP. */
      command(msg->buffer[0], bench_now_us());
      return 0;
    }
  if (inject_read_error)
    {
      inject_read_error = false;
      read_error_hit = true;
      return -EIO;
    }
  if (start) position = 0;
  if (position + msg->length > 2) bench_model_unsupported("bh1750_read_past_result");
  for (unsigned i = 0; i < msg->length; i++)
    {
      msg->buffer[i] = valid ? (position == 0 ? latched >> 8 : latched & 255) : 0;
      position++;
    }
  if (position == 2 && valid)
    {
      acquisitions++;
      acquired_epoch = conversions;
    }
  return 0;
}

static const struct bench_i2c_device device = {.message = message};

static int setup(void)
{
  bench_i2c_init(&bus, &device, NULL, 100000);
  assert(bh1750_register(&bus.dev, BENCH_I2C_ADDRESS, 0) == 0);
  assert(conversions == 0 && acquisitions == 0); /* Frozen factory contract. */
  int fd = open("/dev/uorb/sensor_light0", O_RDONLY);
  assert(fd >= 0);
  return fd;
}

static void sample_observed(int fd, unsigned index, bool permit_failure)
{
  struct sensor_light event;
  stimulus = vectors[index].raw;
  unsigned previous = acquisitions, epoch = acquired_epoch;
  uint64_t start = bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd, &event, sizeof(event), sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - start <= 1000000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return;
    }

  assert(observed_result == sizeof(event));
  assert(bench_now_us() - start <= 1000000); /* Observation, not physical spec. */
  assert(acquisitions > previous && acquired_epoch > epoch && valid);
  assert(isfinite(event.light) && event.light >= vectors[index].lower &&
         event.light <= vectors[index].upper && isnan(event.ir));
  assert(event.timestamp >= sample_time && event.timestamp <= bench_now_us());
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  printf("BENCHSIM {\"event\":\"sample\",\"epoch\":%u,\"raw\":%u,\"lux\":%.7f}\n",
         acquired_epoch, latched, (double)event.light);
}
static void sample(int fd, unsigned index)
{
  sample_observed(fd, index, false);
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  if (!strcmp(argv[1], "model/one_time_retention"))
    {
      stimulus = 1200;
      command(0x01, 0);
      command(7, 0);
      assert(!valid);
      command(0x65, 0);
      command(0x42, 0);
      assert(mtreg == 69);
      command(0x20, 0);
      tick(179999);
      assert(!valid && converting);
      tick(180000);
      assert(valid && !powered && !converting && latched == 1200);
      stimulus = 2400;
      tick(900000);
      assert(latched == 1200);
      command(0x20, 900000);
      tick(1080000);
      assert(latched == 2400 && conversions == 2);
      return 0;
    }
  for (unsigned i = 0; i < BENCH_VECTOR_COUNT; i++)
    if (!strcmp(argv[1], vectors[i].id))
      {
        int fd = setup();
        sample(fd, i);
        assert(close(fd) == 0);
        return 0;
      }
  if (!strcmp(argv[1], "data/stable_change") || !strcmp(argv[1], "os/lifecycle"))
    {
      int fd = setup();
      sample(fd, 1);
      sample(fd, 11);
      if (!strcmp(argv[1], "os/lifecycle"))
        {
          char buffer[sizeof(struct sensor_light)];
          assert(read(fd, buffer, 1) < 0);
          assert(close(fd) == 0);
          unsigned before = bus.messages;
          /* No subscriber remains. Exercise the real file API; the public
           * BH1750 ABI does not require direct fetch(NULL) after close. */
          errno = 0;
          assert(read(fd, buffer, sizeof(buffer)) < 0 && errno == EBADF);
          assert(bus.messages == before);
          tick(bench_now_us());
          assert(!converting && !powered); /* One-time mode stops itself. */
          fd = open("/dev/uorb/sensor_light0", O_RDONLY);
          assert(fd >= 0);
        }
      sample(fd, 16);
      assert(close(fd) == 0);
      return 0;
    }
  if (!strcmp(argv[1], "integrity/command_error") || !strcmp(argv[1], "integrity/read_error"))
    {
      int fd = setup();
      struct sensor_light event, before;
      memset(&event, 0xa5, sizeof(event));
      memcpy(&before, &event, sizeof(event));
      if (!strcmp(argv[1], "integrity/command_error")) bus.next_transfer_error = -EIO;
      else inject_read_error = true;
      sample_observed(fd, 0, true);
      bench_finish_transient(&bus.next_transfer_error);
      printf("BENCHSIM transient_device_read_error consumed=%u\n", read_error_hit);
      inject_read_error = false;
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      assert(close(fd) == 0);
      return 0;
    }
  return 65;
}
