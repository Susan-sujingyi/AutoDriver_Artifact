/* ADT7310 register/serial model, independent of a particular SPI call shape.
 * Datasheet Rev A: serial interface, config register and 16-bit temperature.
 * The shared SIM bus owns OS locking, transfers, timing and mode checks.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/signal.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/adt7310.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "scenario.h"

static struct bench_spi bus;
static uint8_t config;
static unsigned position, address, reset_ones;
static bool reading, configured, resetting;
static uint64_t ready_at;
static int16_t stimulus;
static unsigned temperature_reads;

static void select_sensor(void *context, bool selected)
{
  if (selected)
    {
      position = 0;
      reset_ones = 0;
      resetting = false;
    }
}

static uint8_t exchange_sensor(void *context, uint8_t value)
{
  /* A documented 32-one reset is allowed, but never required. */
  reset_ones = value == 0xff ? reset_ones + 8 : 0;
  if (reset_ones >= 32)
    {
      config = 0;
      configured = false;
      position = 0;
      return 0xff;
    }
  if (position++ == 0)
    {
      if (value == 0xff)
        {
          resetting = true;
          return 0xff;
        }
      assert(!(value & 0x87));
      reading = value & 0x40;
      address = (value >> 3) & 7;
      return 0xff;
    }
  if (resetting)
    {
      return 0xff;
    }
  if (!reading)
    {
      assert(address == 1 && position == 2);
      config = value;
      configured = true;
      ready_at = bench_now_us() + 240000;
      return 0xff;
    }
  switch (address)
    {
      case 0:
        assert(position == 2);
        return 0x70 | ((!configured || bench_now_us() < ready_at) ? 0x80 : 0);
      case 1:
        assert(position == 2);
        return config;
      case 2:
        assert(configured && (config & 0xe0) == 0x80);
        assert(bench_now_us() >= ready_at);
        assert(position == 2 || position == 3);
        if (position == 3) temperature_reads++;
        return position == 2 ? (uint16_t)stimulus >> 8 : (uint16_t)stimulus & 255;
      case 3:
        assert(position == 2);
        return 0xc3;
      default:
        assert(!"Unsupported device register in the ADT7310 model");
        return 0xff;
    }
}

static const struct bench_spi_device device = {select_sensor, exchange_sensor};

static int setup(void)
{
  bench_spi_init(&bus, &device, NULL, SPIDEV_TEMPERATURE(0),
                 BENCH_SPI_MODE, BENCH_SPI_MAX_HZ);
  assert(adt7310_register(&bus.dev, SPIDEV_TEMPERATURE(0), 0) == 0);
  int fd = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
  assert(fd >= 0);
  return fd;
}

static void sample_observed(int fd, float expected, bool permit_failure)
{
  struct sensor_temp event;
  memset(&event, 0xa5, sizeof(event));
  uint64_t start = bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd, &event, sizeof(event), sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - start <= 1100000);
      assert(!bus.selected && !nxmutex_is_locked(&bus.mutex));
      return;
    }

  assert(observed_result == sizeof(event));
  assert(bench_now_us() - start <= 1100000);
  assert(temperature_reads > 0);
  assert(isfinite(event.temperature) && event.temperature == expected);
  assert(event.timestamp >= ready_at && event.timestamp <= bench_now_us());
  assert(!bus.selected && !nxmutex_is_locked(&bus.mutex));
  printf("BENCHSIM {\"event\":\"sample\",\"raw\":%d,\"value\":%.7f,"
         "\"timestamp_us\":%llu}\n", stimulus, (double)event.temperature,
         (unsigned long long)event.timestamp);
}
static void sample(int fd, float expected)
{
  sample_observed(fd, expected, false);
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2)
    {
      return 64;
    }
  for (unsigned i = 0; i < BENCH_VECTOR_COUNT; i++)
    {
      if (!strcmp(argv[1], vectors[i].id))
        {
          stimulus = vectors[i].raw;
          int fd = setup();
          sample(fd, vectors[i].expected);
          assert(close(fd) == 0);
          return 0;
        }
    }
  if (!strcmp(argv[1], "data/stable_change"))
    {
      stimulus = vectors[1].raw;
      int fd = setup();
      sample(fd, vectors[1].expected);
      stimulus = vectors[2].raw;
      assert(nxsig_usleep(1000000) == 0);
      sample(fd, vectors[2].expected);
      assert(close(fd) == 0);
      return 0;
    }
  if (!strcmp(argv[1], "os/lifecycle"))
    {
      stimulus = vectors[1].raw;
      int fd = setup();
      char buffer[sizeof(struct sensor_temp)];
      assert(read(fd, buffer, 1) < 0);
      sample(fd, vectors[1].expected);
      assert(close(fd) == 0);
      unsigned before = bus.words;
      bench_sensor_check_disabled(SENSOR_TYPE_AMBIENT_TEMPERATURE, 0, sizeof(struct sensor_temp));
      assert(bus.words == before);
      fd = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
      assert(fd >= 0);
      sample(fd, vectors[1].expected);
      assert(close(fd) == 0);
      return 0;
    }
  if (!strcmp(argv[1], "integrity/invalid_acquisition"))
    {
      stimulus = vectors[1].raw;
      int fd = setup();
      bus.next_lock_error = -EIO;
      sample_observed(fd, vectors[1].expected, true);
      bench_finish_transient(&bus.next_lock_error);
      assert(close(fd) == 0);
      return 0;
    }
  return 65;
}
