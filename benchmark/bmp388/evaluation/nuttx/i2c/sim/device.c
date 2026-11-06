/* BMP388 DS001-07 register/epoch model using the real NuttX SIM platform.
 * Write framing follows datasheet section 5: register/address pairs. Read
 * bursts increment addresses and snapshot a coherent conversion epoch.
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
#include <nuttx/sensors/bmp388.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "vectors.h"
#include "scenario.h"

struct acquisition
{
  unsigned epoch, mask, eligible;
  const struct vector *vector;
  uint64_t at[2];
};
static struct acquisition history[64];
static struct bench_i2c bus;
static uint8_t regs[128], pointer, error;
static const struct vector *input = &vectors[0], *frame = &vectors[0];
/* BMP-TIME starts after power stabilization. Only an explicit soft reset
 * below enters the separately documented 2ms model settling interval. */
static uint64_t ready_at, next_conversion;
static unsigned epoch, ready, delivered[2], write_offset;
static int fds[2];

static void tick(void)
{
  if (next_conversion && bench_now_us() >= next_conversion)
    {
      unsigned n = (bench_now_us() - next_conversion) / 40000 + 1;
      epoch += n;
      next_conversion += (uint64_t)n * 40000;
      frame = input;
      ready = 3;
    }
}

static void configuration(void)
{
  assert(regs[0x1b] == 0x33 && (regs[0x1c] & 0x3f) == 2 && (regs[0x1d] & 31) == 3);
  assert((regs[0x1f] & 14) == 0 && (regs[0x17] & 1) == 0 && (regs[0x19] & 0x58) == 0);
}

static void write_register(unsigned reg, uint8_t value)
{
  assert(reg < 128);
  if (reg == 0x7e)
    {
      if (value == 0xb0) return; /* FIFO flush is permitted but unnecessary. */
      assert(value == 0xb6);
      memset(regs, 0, sizeof(regs));
      regs[0x19] = 2;
      ready = error = 0;
      next_conversion = 0;
      ready_at = bench_now_us() + 2000;
      memset(history, 0, sizeof(history));
      return;
    }
  switch (reg)
    {
      case 0x1b: assert(!(value & ~0x33)); break;
      case 0x1c: assert(!(value & 0xc0)); break;
      case 0x1d: assert(!(value & 0xe0)); break;
      case 0x1f: assert(!(value & ~14)); break;
      case 0x17: case 0x18: assert(!(value & ~31)); break;
      case 0x19: assert(!(value & ~0x5f)); break;
      case 0x1a: assert(!(value & 7)); break;
      default: assert(!"Unsupported BMP388 write register");
    }
  regs[reg] = value;
  if (reg == 0x1b)
    {
      ready = 0;
      next_conversion = (value & 3) == 3 && (value & 0x30) ? bench_now_us() + 12480 : 0;
    }
}

static void read_registers(uint8_t *data, unsigned count)
{
  assert(pointer + count <= 128);
  /* DS001-07 section 4.3.6 defines these as a sensor clock, not zero-filled
   * storage. Until that clock/latching/reset contract is modeled, do not
   * attribute failures of legal clock-based drivers to their implementation. */
  if (pointer <= 0x0e && pointer + count > 0x0c)
    bench_model_unsupported("bmp388_sensor_time_clock_not_modeled");
  unsigned mask = 0, before = ready;
  bool invalid = (error & 7) != 0;
  for (unsigned i = 0; i < count; i++)
    {
      unsigned reg = pointer + i;
      uint8_t value = regs[reg];
      if (reg == 0) value = 0x50;
      if (reg == 2)
        {
          value = error;
          error &= 1;
          if (value & 7) memset(history, 0, sizeof(history));
        }
      if (reg == 3) value = 16 | ((ready & 1) ? 64 : 0) | ((ready & 2) ? 32 : 0);
      if (reg >= 4 && reg <= 9)
        {
          value = frame->raw[reg - 4];
          mask |= 1u << (reg - 4);
        }
      if (reg >= 0x31 && reg <= 0x45) value = input->cal[reg - 0x31];
      data[i] = value;
    }
  if (mask && epoch && !invalid)
    {
      struct acquisition *a = &history[epoch % 64];
      if (a->epoch != epoch) *a = (struct acquisition){.epoch = epoch, .vector = frame};
      a->mask |= mask;
      if ((mask & 0x38) && (before & 1)) a->eligible |= 1;
      if ((mask & 7) && (before & 2)) a->eligible |= 2;
      for (unsigned ch = 0; ch < 2; ch++)
        {
          unsigned needed = ch ? 0x3f : 0x38;
          if (!a->at[ch] && (a->mask & needed) == needed) a->at[ch] = bench_now_us();
        }
    }
  if (mask & 0x38) ready &= ~1u;
  if (mask & 7) ready &= ~2u;
  pointer += count;
}

static int message(void *context, struct i2c_msg_s *msg,
                    bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && msg->length > 0);
  if (bench_now_us() < ready_at) return -ENXIO;
  tick();
  if (msg->flags & I2C_M_READ)
    {
      read_registers(msg->buffer, msg->length);
      return 0;
    }
  if (start) write_offset = 0;
  for (unsigned i = 0; i < msg->length; i++)
    {
      if (write_offset++ % 2 == 0) pointer = msg->buffer[i];
      else write_register(pointer, msg->buffer[i]);
    }
  return 0;
}

static int acknowledge_address(void *context, uint16_t address, bool reading)
{
  assert(address == BENCH_I2C_ADDRESS);
  if (bench_now_us() < ready_at)
    bench_model_unsupported("bmp388_before_modeled_reset_completion");
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
  regs[0x19] = 2;
  bench_i2c_init(&bus, &device, NULL, BENCH_I2C_MAX_HZ);
  assert(bmp388_register(&bus.dev, BENCH_I2C_ADDRESS, 0) == 0);
  assert(bench_now_us() - start <= 100000);
  configuration();
  fds[0] = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
  fds[1] = open("/dev/uorb/sensor_baro0", O_RDONLY);
  assert(fds[0] >= 0 && fds[1] >= 0);
}

static float sample_observed(unsigned channel, bool permit_failure)
{
  union {struct sensor_temp t; struct sensor_baro p;} event;
  size_t size = channel ? sizeof(event.p) : sizeof(event.t);
  uint64_t start = bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fds[channel], &event, size, sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - start <= 100000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return NAN;
    }

  assert(observed_result == size);
  assert(bench_now_us() - start <= 100000);
  configuration();
  uint64_t timestamp = channel ? event.p.timestamp : event.t.timestamp;
  float value = channel ? event.p.pressure : event.t.temperature;
  if (channel) assert(isnan(event.p.temperature));
  assert(isfinite(value) && timestamp <= bench_now_us());
  unsigned matched = 0;
  for (unsigned i = 0; i < 64; i++)
    {
      struct acquisition *a = &history[i];
      unsigned needed = channel ? 0x3f : 0x38;
      if (!a->vector || !(a->eligible & (1u << channel)) || a->epoch <= delivered[channel] ||
          a->at[channel] > timestamp || (a->mask & needed) != needed) continue;
      long double expected = a->vector->expected[channel];
      long double tolerance = (channel ? 1.0L : 0.01L) + 2 * FLT_EPSILON * fmaxl(1, fabsl(expected));
      if (fabsl(value - expected) <= tolerance && a->epoch > matched) matched = a->epoch;
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
  union {struct sensor_temp t; struct sensor_baro p;} event, before;
  size_t size = channel ? sizeof(event.p) : sizeof(event.t);
  memset(&event, 0xa5, sizeof(event));
  memcpy(&before, &event, sizeof(event));
  uint64_t start = bench_now_us();
  assert(read(fds[channel], &event, size) < 0);
  assert(!memcmp(&event, &before, sizeof(event)));
  assert(bench_now_us() - start <= 100000);
}

static void model_check(void)
{
  /* These checks exercise only the model, never the candidate. */
  assert(acknowledge_address(NULL, BENCH_I2C_ADDRESS, true) == 0);
  epoch = 1;
  ready = 3;
  uint8_t bytes[8];
  pointer = 7;
  read_registers(bytes, 3);
  uint64_t first = history[1].at[0];
  bench_wire_delay(100, 1000000);
  pointer = 4;
  read_registers(bytes, 6);
  assert(history[1].at[0] == first && history[1].at[1] == bench_now_us());
  memset(history, 0, sizeof(history));
  ready = 3;
  error = 2;
  pointer = 2;
  read_registers(bytes, 8);
  assert(error == 0 && history[1].epoch == 0);
  write_register(0x7e, 0xb6);
  assert(bench_now_us() < ready_at);
  bench_wire_delay(2000, 1000000);
  assert(acknowledge_address(NULL, BENCH_I2C_ADDRESS, true) == 0);
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  if (!strcmp(argv[1], "model/acquisition_history"))
    {
      bench_wire_delay(1, 1000000); /* Nonzero acquisition timestamp. */
      model_check();
      return 0;
    }
  for (unsigned i = 0; i < VECTOR_COUNT; i++)
    if (!strncmp(argv[1], "numeric/", 8) && !strcmp(argv[1] + 8, vectors[i].id))
      {
        input = frame = &vectors[i];
        setup(); sample(0); sample(1);
        return 0;
      }
  setup();
  if (!strcmp(argv[1], "data/stable_change"))
    {
      sample(0); sample(1);
      for (unsigned i = 0; i < VECTOR_COUNT; i++)
        if (!memcmp(vectors[0].cal, vectors[i].cal, 21))
          {
            input = &vectors[i];
            assert(nxsig_usleep(80000) == 0);
            sample(1); sample(0); sample(1);
          }
      return 0;
    }
  if (!strcmp(argv[1], "os/lifecycle"))
    {
      char buffer[sizeof(struct sensor_baro)];
      assert(read(fds[0], buffer, 1) < 0 && read(fds[1], buffer, 1) < 0);
      assert(close(fds[0]) == 0 && close(fds[1]) == 0);
      unsigned before = bus.messages;
      bench_sensor_check_disabled(SENSOR_TYPE_AMBIENT_TEMPERATURE, 0, sizeof(struct sensor_temp));
      bench_sensor_check_disabled(SENSOR_TYPE_BAROMETER, 0, sizeof(struct sensor_baro));
      assert(bus.messages == before);
      fds[0] = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
      fds[1] = open("/dev/uorb/sensor_baro0", O_RDONLY);
      assert(fds[0] >= 0 && fds[1] >= 0);
      sample(0); sample(1);
      return 0;
    }
  if (!strcmp(argv[1], "integrity/invalid_acquisition"))
    {
      error = 1; failure(0); failure(1);
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
