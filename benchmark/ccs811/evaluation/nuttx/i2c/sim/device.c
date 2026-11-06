/* CCS811 mailbox/application model, ams DS000459 v1-06. Real NuttX supplies
 * all OS services. I2C framing/time are shared with other SIM devices.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/signal.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/ccs811.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "scenario.h"

struct acquisition
{
  unsigned epoch, channels;
  uint16_t words[2];
  uint64_t at[2];
};
static struct bench_i2c bus;
static struct acquisition history[32];
static uint16_t stimulus[2] = {800, 100}, frame[2];
static unsigned epoch, delivered[2], write_offset, read_offset;
static uint64_t ready_at = 20000, next_conversion;
static uint8_t pointer, mode, error, reset_bytes[4];
static bool app, ready;
static int fds[2];

static void tick(void)
{
  if (next_conversion && bench_now_us() >= next_conversion)
    {
      unsigned count = (bench_now_us() - next_conversion) / 1000000 + 1;
      epoch += count;
      next_conversion += (uint64_t)count * 1000000;
      memcpy(frame, stimulus, sizeof(frame));
      ready = true;
    }
}

static uint8_t status(void)
{
  return (app ? 0x80 : 0) | 0x10 | (ready ? 8 : 0) | (error ? 1 : 0);
}

static uint8_t read_byte(unsigned offset)
{
  uint8_t bytes[8] = {frame[0] >> 8, frame[0], frame[1] >> 8, frame[1],
                      status(), error, 0xa5, 0x5a};
  switch (pointer)
    {
      case 0: assert(offset == 0); return status();
      case 1: assert(offset == 0); return mode;
      case 2: assert(offset < 8 && app); return bytes[offset];
      case 3: assert(offset < 2); return bytes[offset + 6];
      case 0x20: assert(offset == 0); return 0x81;
      case 0x21: assert(offset == 0); return 0x12;
      case 0x23: case 0x24: assert(offset < 2); return offset ? 0 : 0x20;
      case 0xe0:
        assert(offset == 0);
        uint8_t result = error;
        error = 0;
        memset(history, 0, sizeof(history));
        return result;
      default: assert(!"Unsupported CCS811 mailbox in device model"); return 0;
    }
}

static int acknowledge_address(void *context, uint16_t address, bool reading)
{
  assert(address == BENCH_I2C_ADDRESS);
  if (bench_now_us() < ready_at)
    {
      printf("BENCHSIM {\"event\":\"device_nack\",\"reason\":\"boot_guard\","
             "\"now_us\":%llu,\"ready_us\":%llu}\n",
             (unsigned long long)bench_now_us(), (unsigned long long)ready_at);
      return -ENXIO;
    }
  return 0;
}

static int message(void *context, struct i2c_msg_s *msg,
                    bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS);
  assert(msg->length > 0 && msg->length <= 8);
  tick();
  assert(bench_now_us() >= ready_at);
  if (msg->flags & I2C_M_READ)
    {
      if (start) read_offset = 0;
      for (unsigned i = 0; i < msg->length; i++)
        msg->buffer[i] = read_byte(read_offset++);
      if (error) memset(history, 0, sizeof(history));
      if (pointer == 2)
        {
          if (epoch && !error && (mode & 0x70) == 0x10)
            {
              struct acquisition *a = &history[epoch % 32];
              if (a->epoch != epoch) *a = (struct acquisition){.epoch = epoch};
              unsigned available = read_offset >= 4 ? 2 : read_offset >= 2 ? 1 : 0;
              for (unsigned ch = a->channels; ch < available; ch++)
                a->at[ch] = bench_now_us();
              if (available > a->channels) a->channels = available;
              memcpy(a->words, frame, sizeof(frame));
            }
          if (stop) ready = false;
        }
      return 0;
    }
  if (start) write_offset = 0;
  for (unsigned i = 0; i < msg->length; i++)
    {
      uint8_t value = msg->buffer[i];
      if (write_offset++ == 0)
        {
          pointer = value;
          if (pointer == 0xf4)
            {
              app = true;
              ready_at = bench_now_us() + 1000;
            }
        }
      else if (pointer == 1)
        {
          assert(app && write_offset == 2);
          mode = value;
          next_conversion = bench_now_us() + 1000000;
          ready = false;
        }
      else if (pointer == 0xff)
        {
          assert(write_offset <= 5);
          reset_bytes[write_offset - 2] = value;
          if (write_offset == 5)
            {
              assert(!memcmp(reset_bytes, (uint8_t[]){0x11, 0xe5, 0x72, 0x8a}, 4));
              app = ready = false;
              mode = error = 0;
              next_conversion = 0;
              ready_at = bench_now_us() + 2000;
              memset(history, 0, sizeof(history));
            }
        }
      else assert(!"Unexpected CCS811 write mailbox");
    }
  return 0;
}

/* The public event contract invalidates cached success on known bus
 * errors. Keep no earlier acquisition witness after an actual failed call.
 * A successful retry can record a new complete acquisition normally. */
static void transfer_failed(void *context, const struct i2c_msg_s *messages,
                            int count, int error)
{
  assert(error < 0);
  memset(history, 0, sizeof(history));
}
static const struct bench_i2c_device device = {.message = message, .address = acknowledge_address, .transfer_failed=transfer_failed};

static void setup(void)
{
  uint64_t start = bench_now_us();
  bench_i2c_init(&bus, &device, NULL, BENCH_I2C_MAX_HZ);
  assert(ccs811_register(&bus.dev, BENCH_I2C_ADDRESS, 0) == 0);
  assert(bench_now_us() - start <= 100000);
  assert(app && (mode & 0x70) == 0x10);
  fds[0] = open("/dev/uorb/sensor_co20", O_RDONLY);
  fds[1] = open("/dev/uorb/sensor_tvoc0", O_RDONLY);
  assert(fds[0] >= 0 && fds[1] >= 0);
}

static double expected(unsigned channel, uint16_t word)
{
  return channel ? word / 1000.0 : word;
}

static float sample_observed(unsigned channel, bool permit_failure)
{
  union {struct sensor_co2 c; struct sensor_tvoc v;} event;
  size_t size = channel ? sizeof(event.v) : sizeof(event.c);
  uint64_t start = bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fds[channel], &event, size, sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - start <= 2100000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return NAN;
    }

  assert(observed_result == size);
  assert(bench_now_us() - start <= 2100000);
  uint64_t timestamp = channel ? event.v.timestamp : event.c.timestamp;
  float value = channel ? event.v.tvoc : event.c.co2;
  assert(isfinite(value) && timestamp <= bench_now_us());
  unsigned matched = 0;
  for (unsigned i = 0; i < 32; i++)
    {
      struct acquisition *a = &history[i];
      double physical = expected(channel, a->words[channel]);
      if (a->epoch > delivered[channel] && a->channels > channel && a->at[channel] <= timestamp &&
          fabs(value - physical) <= 2 * FLT_EPSILON * fmax(1, fabs(physical)))
        matched = matched > a->epoch ? matched : a->epoch;
    }
  assert(matched);
  delivered[channel] = matched;
  printf("BENCHSIM {\"event\":\"sample\",\"channel\":%u,\"value\":%.6f,"
         "\"epoch\":%u,\"timestamp_us\":%llu}\n", channel, (double)value, matched,
         (unsigned long long)timestamp);
  return value;
}
static float sample(unsigned channel)
{
  return sample_observed(channel, false);
}

static void failure(unsigned channel)
{
  struct sensor_co2 event, before;
  memset(&event, 0xa5, sizeof(event));
  memcpy(&before, &event, sizeof(event));
  uint64_t start = bench_now_us();
  assert(read(fds[channel], &event, sizeof(event)) < 0);
  assert(!memcmp(&event, &before, sizeof(event)));
  assert(bench_now_us() - start <= 2100000);
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  if (!strcmp(argv[1], "model/acquisition_history"))
    {
      uint8_t bytes[4];
      struct i2c_msg_s msg = {.addr = BENCH_I2C_ADDRESS, .flags = I2C_M_READ,
                              .buffer = bytes, .length = 4};
      ready_at = 0; epoch = 1; app = true; mode = 0x10; pointer = 2;
      bench_wire_delay(1, 1000000);
      assert(message(NULL, &msg, true, false, true) == 0);
      uint64_t first = history[1].at[1];
      bench_wire_delay(100, 1000000);
      msg.length = 2;
      assert(message(NULL, &msg, true, false, true) == 0);
      assert(history[1].channels == 2 && history[1].at[1] == first);
      error = 1;
      assert(message(NULL, &msg, true, false, true) == 0);
      assert(history[1].epoch == 0);
      return 0;
    }
  for (unsigned i = 0; i < BENCH_VECTOR_COUNT; i++)
    if (!strcmp(argv[1], vectors[i].id))
      {
        memcpy(stimulus, vectors[i].words, sizeof(stimulus));
        setup();
        for (unsigned ch = 0; ch < 2; ch++)
          if (vectors[i].valid[ch])
            {
              float value = sample(ch);
              assert(fabs(value - vectors[i].expected[ch]) <=
                     2 * FLT_EPSILON * fmax(1, fabs(vectors[i].expected[ch])));
            }
          else failure(ch);
        return 0;
      }
  setup();
  if (!strcmp(argv[1], "data/stable_change"))
    {
      sample(0); sample(1);
      stimulus[0] = 1200; stimulus[1] = 240;
      assert(nxsig_usleep(2000000) == 0);
      bool seen[2] = {false, false};
      for (unsigned n = 0; n < 3; n++)
        for (unsigned ch = 0; ch < 2; ch++)
          {
            float value = sample(ch);
            if (fabs(value - expected(ch, stimulus[ch])) < 0.00001) seen[ch] = true;
          }
      assert(seen[0] && seen[1]);
      return 0;
    }
  if (!strcmp(argv[1], "os/lifecycle"))
    {
      char buffer[sizeof(struct sensor_co2)];
      assert(read(fds[0], buffer, 1) < 0);
      assert(read(fds[1], buffer, 1) < 0);
      assert(close(fds[0]) == 0 && close(fds[1]) == 0);
      unsigned before = bus.messages;
      bench_sensor_check_disabled(SENSOR_TYPE_CO2, 0, sizeof(struct sensor_co2));
      bench_sensor_check_disabled(SENSOR_TYPE_TVOC, 0, sizeof(struct sensor_tvoc));
      assert(bus.messages == before);
      fds[0] = open("/dev/uorb/sensor_co20", O_RDONLY);
      fds[1] = open("/dev/uorb/sensor_tvoc0", O_RDONLY);
      assert(fds[0] >= 0 && fds[1] >= 0);
      sample(0); sample(1);
      return 0;
    }
  if (!strcmp(argv[1], "integrity/invalid_acquisition"))
    {
      error = 1; failure(0);
      error = 1; failure(1);
      return 0;
    }
  if (!strcmp(argv[1], "integrity/transport_error"))
    {
      bus.next_transfer_error = -EIO; sample_observed(0,true); bench_finish_transient(&bus.next_transfer_error);
      bus.next_transfer_error = -EIO; sample_observed(1,true); bench_finish_transient(&bus.next_transfer_error);
      return 0;
    }
  return 65;
}
