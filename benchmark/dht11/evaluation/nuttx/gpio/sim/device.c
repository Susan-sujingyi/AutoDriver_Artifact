/* DHT11 pulse generator. Device framing/timing follow the original datasheet;
 * real NuttX handles allocations, locks, sleeps, nodes and file reads.
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
#include <nuttx/sensors/dht11.h>
#include "bus.h"
#include "wave.h"
#include "observer.h"
#include "scenario.h"

static uint8_t wire[8001];
static unsigned temperature = 24, humidity = 53;
static unsigned starts, captures;
static uint64_t low_at, last_capture;
static bool low, corrupt, no_response;
static int fds[2];

static void append(unsigned *position, bool value, unsigned width)
{
  assert(*position + width <= sizeof(wire));
  memset(wire + *position, value, width);
  *position += width;
}

static int drive(void *context, bool value)
{
  if (value)
    {
      assert(!low && bench_now_us() >= 1000000);
      assert(!captures || bench_now_us() - last_capture >= 1000000);
      low_at = bench_now_us();
      starts++;
    }
  low = value;
  return 0;
}

static int begin(void *context)
{
  unsigned position = 0;
  uint8_t frame[5] = {humidity, 0, temperature, 0, humidity + temperature};
  assert(low && bench_now_us() - low_at > 18000);
  assert(bench_now_us() - low_at < 100000);
  if (corrupt)
    {
      frame[4] ^= 1;
    }
  memset(wire, 1, sizeof(wire));
  if (!no_response)
    {
      append(&position, true, 30);
      append(&position, false, 80);
      append(&position, true, 80);
      for (unsigned i = 0; i < 40; i++)
        {
          append(&position, false, 50);
          append(&position, true, (frame[i / 8] & (0x80 >> (i % 8))) ? 70 : 26);
        }
      append(&position, false, 50);
    }
  return 0;
}

static bool level(void *context, uint32_t elapsed)
{
  assert(elapsed < sizeof(wire));
  return wire[elapsed];
}

static void end(void *context)
{
  captures++;
  last_capture = bench_now_us();
}

static const struct bench_wave_ops operations = {drive, begin, level, end};

static void setup(void)
{
  bench_wave_install(&operations, NULL);
  assert(dht11_register(0) == 0 && starts == 0);
  fds[0] = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
  fds[1] = open("/dev/uorb/sensor_humi0", O_RDONLY);
  assert(fds[0] >= 0 && fds[1] >= 0);
}

static void sample(unsigned channel, float expected)
{
  union {struct sensor_temp t; struct sensor_humi h;} event;
  size_t size = channel ? sizeof(event.h) : sizeof(event.t);
  uint64_t start = bench_now_us();
  unsigned before = captures;
  assert(read(fds[channel], &event, size) == size);
  assert(bench_now_us() - start <= 2000000 && !low);
  /* The frozen public DHT11 contract explicitly requires a fresh frame for
   * each node read. This is not an inferred universal no-cache policy. */
  assert(captures > before);
  float value = channel ? event.h.humidity : event.t.temperature;
  uint64_t timestamp = channel ? event.h.timestamp : event.t.timestamp;
  assert(isfinite(value) && value == expected);
  assert(timestamp >= last_capture && timestamp <= bench_now_us());
  printf("BENCHSIM {\"event\":\"sample\",\"channel\":%u,\"value\":%.2f,"
         "\"timestamp_us\":%llu}\n", channel, (double)value,
         (unsigned long long)timestamp);
}

static void both(unsigned index)
{
  sample(0, vectors[index].expected_temperature);
  sample(1, vectors[index].expected_humidity);
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
          temperature = vectors[i].temperature;
          humidity = vectors[i].humidity;
          setup();
          both(i);
          return 0;
        }
    }
  temperature = vectors[0].temperature;
  humidity = vectors[0].humidity;
  setup();
  if (!strcmp(argv[1], "data/stable_change"))
    {
      both(0);
      temperature = vectors[1].temperature;
      humidity = vectors[1].humidity;
      both(1);
      return 0;
    }
  if (!strcmp(argv[1], "os/lifecycle"))
    {
      char buffer[sizeof(struct sensor_temp)];
      assert(read(fds[0], buffer, 1) < 0);
      assert(close(fds[0]) == 0 && close(fds[1]) == 0);
      unsigned before = starts;
      bench_sensor_check_disabled(SENSOR_TYPE_AMBIENT_TEMPERATURE, 0, sizeof(struct sensor_temp));
      bench_sensor_check_disabled(SENSOR_TYPE_RELATIVE_HUMIDITY, 0, sizeof(struct sensor_humi));
      assert(starts == before);
      fds[0] = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
      fds[1] = open("/dev/uorb/sensor_humi0", O_RDONLY);
      assert(fds[0] >= 0 && fds[1] >= 0);
      both(0);
      return 0;
    }
  if (!strcmp(argv[1], "integrity/invalid_acquisition") ||
      !strcmp(argv[1], "diagnostic/no_response"))
    {
      corrupt = !strcmp(argv[1], "integrity/invalid_acquisition");
      no_response = !corrupt;
      for (unsigned ch = 0; ch < 2; ch++)
        {
          struct sensor_temp event, before;
          memset(&event, 0xa5, sizeof(event));
          memcpy(&before, &event, sizeof(event));
          uint64_t start = bench_now_us();
          assert(read(fds[ch], &event, sizeof(event)) < 0);
          assert(!memcmp(&event, &before, sizeof(event)));
          assert(bench_now_us() - start <= 2000000 && !low);
        }
      return 0;
    }
  return 65;
}
