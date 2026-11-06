/* Bosch BST-BME280-DS001-23 sections 3.3, 4.1, 5.4, 6.2/6.3 and 9.1.
 * One device model serves both buses. NuttX owns Sensor, locks, allocation,
 * file operations and scheduling. No replacement OS declarations/functions.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/sensors/sensor.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "vectors.h"
#include "scenario.h"
#if BENCH_SPI
#include <nuttx/sensors/bme280_spi.h>
static struct bench_spi bus;
#else
#include <nuttx/sensors/bme280.h>
static struct bench_i2c bus;
#endif

struct frame {unsigned epoch, vector, invalid; uint64_t completed;};
struct acquisition {struct frame frame; unsigned mask;};
static struct acquisition history[64];
static struct frame output, shadow;
static uint8_t regs[256], pointer, effective_humidity;
static unsigned input, pending_input, epochs, delivered[3], write_offset;
static uint64_t nvm_ready, conversion_ready;
static bool shadowed, measuring, nvm_stuck, measurement_stuck, read_stop;
static int skipped = -1;
static const char *paths[] = {"/dev/uorb/sensor_ambient_temp0", "/dev/uorb/sensor_humi0", "/dev/uorb/sensor_baro0"};
static const char *channels[] = {"temperature", "humidity", "pressure"};
static const int types[] = {SENSOR_TYPE_AMBIENT_TEMPERATURE, SENSOR_TYPE_RELATIVE_HUMIDITY, SENSOR_TYPE_BAROMETER};
union sample_event {struct sensor_temp t; struct sensor_humi h; struct sensor_baro p;};

static void tick(uint64_t now)
{
  if (measuring && !measurement_stuck && now >= conversion_ready)
    {
      output = (struct frame){.epoch = ++epochs, .vector = pending_input,
                              .invalid = skipped < 0 ? 0 : 1u << skipped,
                              .completed = conversion_ready};
      measuring = false;
      regs[0xf4] &= ~3;
    }
}

static void configuration(uint8_t control)
{
  assert((control >> 5) == 1 && ((control >> 2) & 7) >= 5);
  assert(effective_humidity == 1 && !(regs[0xf5] & 0x1d));
  assert((control & 3) == 1 || (control & 3) == 2);
}

static void write_register(unsigned reg, uint8_t value)
{
  uint64_t now = bench_now_us();
  tick(now);
  if (reg == 0xe0)
    {
      if (value != 0xb6) return;
      regs[0xf2] = regs[0xf4] = regs[0xf5] = effective_humidity = 0;
      nvm_ready = now + 2000;
      measuring = shadowed = false;
      output = (struct frame){0};
      memset(history, 0, sizeof(history));
      return;
    }
  if (reg != 0xf2 && reg != 0xf4 && reg != 0xf5)
    bench_model_unsupported("bme280_register_write_not_modeled");
  if (nvm_stuck || now < nvm_ready) bench_model_unsupported("bme280_write_during_nvm_copy");
  if (measuring)
    {
      if (reg == 0xf4 && (value & 0xfc) == (regs[reg] & 0xfc) && (value & 3) != 3)
        return; /* Forced-rate saturation does not restart a running conversion. */
      if (regs[reg] == value) return;
      bench_model_unsupported("bme280_configuration_during_conversion");
    }
  regs[reg] = value;
  if (reg == 0xf4)
    {
      effective_humidity = regs[0xf2] & 7;
      if (!(value & 3)) return;
      configuration(value);
      pending_input = input;
      conversion_ready = now + 43800;
      measuring = true;
    }
}

static uint8_t read_register(unsigned reg)
{
  assert(reg < 256);
  if (reg == 0xf3) return ((nvm_stuck || bench_now_us() < nvm_ready) ? 1 : 0) | (measuring ? 8 : 0);
  if (reg == 0xe0) return 0;
  if (reg >= 0xf7 && reg <= 0xfe)
    {
      if (!shadowed) {shadow = output; shadowed = true;}
      if (!shadow.epoch)
        return (reg == 0xf7 || reg == 0xfa || reg == 0xfd) ? 0x80 : 0;
      struct acquisition *a = &history[shadow.epoch % 64];
      if (a->frame.epoch != shadow.epoch) *a = (struct acquisition){.frame = shadow};
      a->mask |= 1u << (reg - 0xf7);
      unsigned ch = reg < 0xfa ? 2 : reg < 0xfd ? 0 : 1;
      if (shadow.invalid & (1u << ch)) return (reg == 0xf7 || reg == 0xfa || reg == 0xfd) ? 0x80 : 0;
      return vectors[shadow.vector].regs[reg];
    }
  if ((reg >= 0x88 && reg <= 0xa1) || (reg >= 0xe1 && reg <= 0xe7))
    return nvm_stuck || bench_now_us() < nvm_ready ? 0 : regs[reg];
  if (reg == 0xd0 || reg == 0xf2 || reg == 0xf4 || reg == 0xf5) return regs[reg];
  bench_model_unsupported("bme280_register_read_not_modeled");
}

#if BENCH_SPI
static bool spi_read;
static void select_device(void *context, bool selected)
{
  if (selected) {write_offset = 0; spi_read = false;}
  else shadowed = false;
}
static uint8_t exchange(void *context, uint8_t value)
{
  tick(bench_now_us());
  if (!write_offset)
    {pointer = value | 0x80; spi_read = (value & 0x80) != 0; write_offset = 1; return 0xa5;}
  if (spi_read) return read_register(pointer++);
  write_register(pointer, value);
  write_offset = 0; /* Multi-write uses address/value pairs, not auto increment. */
  return 0xa5;
}
static const struct bench_spi_device device = {.select = select_device, .exchange = exchange};
static int register_device(int devno) {return bme280_spi_register(&bus.dev, SPIDEV_BAROMETER(0), devno);}
static void idle(void) {assert(!bus.selected && !nxmutex_is_locked(&bus.mutex));}
static unsigned transactions(void) {return bus.words;}
#else
static int message(void *context, struct i2c_msg_s *msg, bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && !(msg->flags & I2C_M_TEN));
  assert(msg->frequency == BENCH_MAX_HZ);
  tick(bench_now_us());
  if (msg->flags & I2C_M_READ) read_stop = stop;
  else
    {
      if (start) write_offset = 0;
      for (unsigned i = 0; i < msg->length; i++)
        {
          if (write_offset++ % 2 == 0) pointer = msg->buffer[i];
          else write_register(pointer, msg->buffer[i]);
        }
      if (stop) shadowed = false;
    }
  return 0;
}
static int read_byte(void *context, const struct i2c_msg_s *msg, unsigned index, uint8_t *value)
{
  tick(bench_now_us());
  *value = read_register(pointer++);
  if (read_stop && index + 1 == msg->length) shadowed = false;
  return 0;
}
static const struct bench_i2c_device device = {.message = message, .read_byte = read_byte};
static int register_device(int devno) {return bme280_register(&bus.dev, BENCH_I2C_ADDRESS, devno);}
static void idle(void) {assert(!bus.active && !nxmutex_is_locked(&bus.mutex));}
static unsigned transactions(void) {return bus.messages;}
#endif

static size_t event_size(unsigned ch)
{return ch == 0 ? sizeof(struct sensor_temp) : ch == 1 ? sizeof(struct sensor_humi) : sizeof(struct sensor_baro);}

static void prepare(void)
{
  memcpy(regs, vectors[input].regs, sizeof(regs));
#if BENCH_SPI
  bench_spi_init(&bus, &device, NULL, SPIDEV_BAROMETER(0), BENCH_SPI_MODE, BENCH_MAX_HZ);
#else
  bench_i2c_init(&bus, &device, NULL, BENCH_MAX_HZ);
#endif
}
static bool setup(int *fds, bool invalid_calibration)
{
  uint64_t begin = bench_now_us();
  int result = register_device(0);
  /* Public factory forbids acquisition, not optional identification/config. */
  assert(!measuring && epochs == 0 && bench_now_us() - begin <= 1000000);
  if (result < 0)
    {
      /* Invalid calibration may be rejected eagerly. A failed factory must
       * leave no nodes and release resources, just like every other failure. */
      assert(invalid_calibration);
      for (unsigned ch = 0; ch < 3; ch++)
        {
          assert(!bench_sensor_find(types[ch], 0));
          errno = 0;
          assert(open(paths[ch], O_RDONLY) < 0 && errno == ENOENT);
        }
      idle();
      printf("BENCHSIM invalid_calibration rejected_at=registration result=%d\n", result);
      return false;
    }
  assert(result == 0);
  for (unsigned ch = 0; ch < 3; ch++) {fds[ch] = open(paths[ch], O_RDONLY); assert(fds[ch] >= 0);}
  idle();
  return true;
}

static unsigned sample_observed(int fd, unsigned ch, bool permit_failure)
{
  union sample_event event;
  memset(&event, 0xa5, sizeof(event));
  uint64_t begin = bench_now_us();
  memset(&event, 0xa5, sizeof(event));

  ssize_t result = bench_observe_read(fd, &event, event_size(ch), sizeof(event), permit_failure);
  if (result < 0)
    {
      assert(bench_now_us() - begin <= 1000000);
      idle();
      return 0;
    }

  uint64_t elapsed = bench_now_us() - begin;
  printf("BENCHSIM fetch channel=%u result=%ld errno=%d elapsed_us=%llu\n", ch, (long)result, errno, (unsigned long long)elapsed);
  assert(result == event_size(ch) && elapsed <= 1000000);
  double value = ch == 0 ? event.t.temperature : ch == 1 ? event.h.humidity : event.p.pressure;
  uint64_t stamp = ch == 0 ? event.t.timestamp : ch == 1 ? event.h.timestamp : event.p.timestamp;
  assert(isfinite(value) && stamp <= bench_now_us());
  unsigned match = 0, index = 0, needed = ch == 0 ? 0x38 : ch == 1 ? 0xf8 : 0x3f;
  for (unsigned i = 0; i < 64; i++)
    {
      struct acquisition *a = &history[i];
      unsigned invalid = (1u << ch) | 1; /* H/P also require valid temperature. */
      if (a->frame.epoch > delivered[ch] && a->frame.epoch > match &&
          a->frame.completed >= begin && a->frame.completed <= stamp &&
          !(a->frame.invalid & invalid) && (a->mask & needed) == needed &&
          value >= vectors[a->frame.vector].low[ch] && value <= vectors[a->frame.vector].high[ch])
        {
          if (ch == 2 && !(isfinite(event.p.temperature) &&
              event.p.temperature >= vectors[a->frame.vector].low[0] &&
              event.p.temperature <= vectors[a->frame.vector].high[0])) continue;
          match = a->frame.epoch; index = a->frame.vector;
        }
    }
  assert(match > delivered[ch]);
  delivered[ch] = match;
  idle();
  printf("BENCHSIM sample channel=%u epoch=%u value=%.9f timestamp_us=%llu\n", ch, match, value, (unsigned long long)stamp);
  return index;
}
static unsigned sample(int fd, unsigned ch)
{
  return sample_observed(fd, ch, false);
}
static void failure(int fd, unsigned ch)
{
  union sample_event event, before;
  memset(&event, 0xa5, sizeof(event)); memcpy(&before, &event, sizeof(event));
  uint64_t begin = bench_now_us();
  ssize_t result = read(fd, &event, event_size(ch));
  printf("BENCHSIM negative_fetch result=%ld errno=%d elapsed_us=%llu\n", (long)result, errno, (unsigned long long)(bench_now_us()-begin));
  assert(result < 0 && !memcmp(&event, &before, sizeof(event)));
  assert(bench_now_us() - begin <= 1000000);
  idle();
}

static void model_contract(void)
{
  regs[0xf2] = 1;
  write_register(0xf4, 0x35);
  uint64_t original = conversion_ready;
  bench_wire_delay(1000, 1000000);
  write_register(0xf4, 0x36);
  assert(conversion_ready == original); /* Repeated forced trigger cannot starve. */
  bench_wire_delay(43800, 1000000); tick(bench_now_us());
  assert(output.epoch == 1 && !(regs[0xf4] & 3));
  assert(read_register(0xfa) == vectors[0].regs[0xfa]);
  input = 1;
  write_register(0xf4, 0x36);
  assert(read_register(0xfa) == vectors[0].regs[0xfa]);
  bench_wire_delay(43800, 1000000); tick(bench_now_us());
  assert(output.vector == 1 && read_register(0xfa) == vectors[0].regs[0xfa]);
  shadowed = false;
  assert(read_register(0xfa) == vectors[1].regs[0xfa]);
  write_register(0xe0, 0xb6);
  assert(!output.epoch && read_register(0xfa) == 0x80);
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  const char *name = argv[1];
  int fds[3];
  for (unsigned i = 0; i < BENCH_VECTOR_COUNT; i++)
    for (unsigned ch = 0; ch < 3; ch++)
      {
        char expected[160]; snprintf(expected, sizeof(expected), "numeric/%s/%s", vectors[i].name, channels[ch]);
        if (!strcmp(name, expected))
          {input = i; prepare(); assert(setup(fds, false)); assert(sample(fds[ch], ch) == i); goto done;}
      }
  prepare();
  if (!strcmp(name, "model/shadow_and_forced")) {model_contract(); return 0;}
  if (!strcmp(name, "os/invalid_factory"))
    {
      assert(register_device(-1) < 0);
#if BENCH_SPI
      assert(bme280_spi_register(NULL, SPIDEV_BAROMETER(0), 0) < 0);
#else
      assert(bme280_register(NULL, BENCH_I2C_ADDRESS, 0) < 0);
      assert(bme280_register(&bus.dev, 0x80, 0) < 0);
#endif
      for (unsigned ch = 0; ch < 3; ch++) assert(!bench_sensor_find(types[ch], 0));
      idle(); return 0;
    }
  if (!strncmp(name, "os/registration_collision_", 26))
    {
      unsigned collision = name[26] - '0'; assert(collision < 3);
      static const struct sensor_ops_s ops = {0};
      struct sensor_lowerhalf_s dummy = {.type = types[collision], .ops = &ops};
      assert(sensor_register(&dummy, 0) == 0);
      assert(register_device(0) < 0);
      for (unsigned ch = 0; ch < 3; ch++)
        assert(bench_sensor_find(types[ch], 0) == (ch == collision ? &dummy : NULL));
      sensor_unregister(&dummy, 0); idle(); return 0;
    }
  bool invalid_calibration = !strcmp(name, "integrity/invalid_pressure_calibration");
  if (invalid_calibration) regs[0x8e] = regs[0x8f] = 0;
  if (!setup(fds, invalid_calibration)) return 0;
  if (!strcmp(name, "data/stable_change"))
    {
      for (unsigned ch = 0; ch < 3; ch++) sample(fds[ch], ch);
      input = 1;
      for (unsigned ch = 0; ch < 3; ch++) assert(sample(fds[ch], ch) == 1);
    }
  else if (!strcmp(name, "os/lifecycle"))
    {
      for (unsigned ch = 0; ch < 3; ch++)
        {
          char small[1]; assert(read(fds[ch], small, sizeof(small)) < 0);
          assert(close(fds[ch]) == 0);
          unsigned before = transactions();
          bench_sensor_check_disabled(types[ch], 0, event_size(ch));
          assert(before == transactions());
          sample(fds[(ch + 1) % 3], (ch + 1) % 3);
          fds[ch] = open(paths[ch], O_RDONLY); assert(fds[ch] >= 0); sample(fds[ch], ch);
        }
    }
  else if (!strcmp(name, "integrity/transport_error"))
    {
#if BENCH_SPI
      bus.next_lock_error = -EIO;
#else
      bus.next_transfer_error = -EIO;
#endif
      sample_observed(fds[0], 0, true);
#if BENCH_SPI
      bench_finish_transient(&bus.next_lock_error);
#else
      bench_finish_transient(&bus.next_transfer_error);
#endif
      sample(fds[0], 0);
    }
  else if (!strcmp(name, "integrity/invalid_pressure_calibration")) failure(fds[2], 2);
  else if (!strncmp(name, "integrity/skipped_", 18))
    {
      for (unsigned ch = 0; ch < 3; ch++) if (!strcmp(name + 18, channels[ch])) skipped = ch;
      assert(skipped >= 0); failure(fds[skipped], skipped);
    }
  else if (!strcmp(name, "diagnostic/permanent_busy")) {measurement_stuck = true; failure(fds[0], 0);}
  else if (!strcmp(name, "diagnostic/nvm_busy")) {nvm_stuck = true; failure(fds[0], 0);}
  else return 65;
done:
  for (unsigned ch = 0; ch < 3; ch++) assert(close(fds[ch]) == 0);
  idle(); return 0;
}
