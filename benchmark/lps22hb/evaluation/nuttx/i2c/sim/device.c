/* ST DocID027083 Rev6 pp37-38: BDU output retention until PRESS_OUT_H,
 * single-byte output reads with IF_ADD_INC disabled. Per-channel complete
 * acquisitions are observed without requiring a particular transaction order
 * or a ready-register probe. Real NuttX owns all OS and file operations.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/lps22hb.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "vectors.h"
#include "scenario.h"

struct acquisition {unsigned epoch, vector, mask, eligible; uint64_t at[2];};
static struct acquisition history[32];
static struct bench_i2c bus;
static const char *paths[] = {"/dev/uorb/sensor_ambient_temp0", "/dev/uorb/sensor_baro0"};
static const int types[] = {SENSOR_TYPE_AMBIENT_TEMPERATURE, SENSOR_TYPE_BAROMETER};
static uint8_t regs[128], pointer, ready, overrun;
static unsigned input, frame, epoch, write_offset, delivered[2];
static uint64_t next_conversion, reset_end;
static bool holding, valid, clear_first, fault_hit;
static int fault_register = -1;

static void configuration(void)
{
  assert((regs[16] & 0xfa) == 0x12);
  assert((regs[17] & 0x59) == 0);
  assert((regs[11] & 0xe0) == 0 && regs[24] == 0 && regs[25] == 0);
}

static void tick(uint64_t now)
{
  if (now >= reset_end) regs[17] &= ~4;
  if (next_conversion && now >= next_conversion)
    {
      unsigned count = (now - next_conversion) / 1000000 + 1;
      next_conversion += (uint64_t)count * 1000000;
      if (!holding || !(regs[16] & 2))
        {
          overrun |= ready << 4;
          ready = 3;
          valid = true;
          holding = (regs[16] & 2) != 0;
          epoch += count;
          frame = input;
        }
    }
}

static uint8_t read_register(unsigned reg)
{
  assert(reg < 128);
  if (reg == 39) return ready | overrun;
  if (reg >= 40 && reg <= 44)
    {
      configuration();
      if (valid)
        {
          struct acquisition *a = &history[epoch % 32];
          if (a->epoch != epoch) *a = (struct acquisition){.epoch = epoch, .vector = frame};
          unsigned ch = reg >= 43 ? 0 : 1;
          unsigned needed = ch ? 7 : 24;
          if (ready & (ch ? 1 : 2)) a->eligible |= 1u << ch;
          a->mask |= 1u << (reg - 40);
          if ((a->mask & needed) == needed && !a->at[ch]) a->at[ch] = bench_now_us();
          /* PH must be the last pressure byte. A lone recovery read may
           * release BDU, but does not become a complete pressure sample. */
          if (reg == 42 && (a->mask & 7) != 7) a->eligible &= ~2u;
        }
      if (reg == 42) holding = false;
      if (reg == 44 || (clear_first && reg == 43)) {ready &= ~2; overrun &= ~32;}
      if (reg == 42 || (clear_first && reg == 40)) {ready &= ~1; overrun &= ~16;}
      return valid ? vectors[frame].bytes[reg - 40] : 0;
    }
  if (reg == 15 || reg == 11 || reg == 12 || reg == 13 ||
      (reg >= 16 && reg <= 20) || (reg >= 24 && reg <= 26)) return regs[reg];
  bench_model_unsupported("lps22hb_register_not_modeled");
}

static void write_register(unsigned reg, uint8_t value)
{
  assert(reg < 128);
  if (bench_now_us() < reset_end) bench_model_unsupported("lps22hb_reset_overlap");
  if (reg == 17 && (value & 0x80)) bench_model_unsupported("lps22hb_memory_boot");
  if (reg == 17 && (value & 4))
    {
      memset(regs, 0, sizeof(regs));
      regs[15] = 0xb1; regs[17] = 0x14;
      next_conversion = 0; valid = holding = false; ready = overrun = 0;
      memset(history, 0, sizeof(history));
      reset_end = bench_now_us() + 1000;
      return;
    }
  if (reg == 16 && ((regs[16] ^ value) & 0x70))
    {
      next_conversion = value & 0x70 ? bench_now_us() + 1000000 : 0;
      holding = false;
    }
  if (reg == 11 || reg == 12 || reg == 13 || (reg >= 16 && reg <= 20) ||
      (reg >= 24 && reg <= 26)) regs[reg] = value;
  else bench_model_unsupported("lps22hb_register_write_not_modeled");
}

static int message(void *context, struct i2c_msg_s *msg,
                   bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && !(msg->flags & I2C_M_TEN));
  tick(bench_now_us());
  if (msg->flags & I2C_M_READ)
    {
      if ((int)pointer == fault_register)
        {
          fault_register = -1; fault_hit = true;
          /* No byte of this message was delivered. Previously delivered
           * bytes of the same retained frame remain valid evidence; a legal
           * retry can finish that frame without re-reading its first byte. */
          return -EIO;
        }
      if (pointer >= 40 && pointer <= 44)
        assert(msg->length == 1 && !(regs[17] & 16));
      for (unsigned i = 0; i < msg->length; i++)
        { msg->buffer[i] = read_register(pointer); if (regs[17] & 16) pointer++; }
    }
  else
    {
      if (start) write_offset = 0;
      for (unsigned i = 0; i < msg->length; i++)
        {
          if (write_offset++ == 0) pointer = msg->buffer[i] & 127;
          else {write_register(pointer, msg->buffer[i]); if (regs[17] & 16) pointer++;}
        }
    }
  return 0;
}

static const struct bench_i2c_device device = {.message = message};

static void setup(int *fds)
{
  regs[15] = 0xb1; regs[17] = 0x10;
  bench_i2c_init(&bus, &device, NULL, 100000);
  uint64_t begin = bench_now_us();
  assert(lps22hb_register(&bus.dev, BENCH_I2C_ADDRESS, 0) == 0);
  assert(bench_now_us() - begin <= 100000);
  configuration();
  for (unsigned i = 0; i < 2; i++) {fds[i] = open(paths[i], O_RDONLY); assert(fds[i] >= 0);}
}

static unsigned sample_observed(int fd, unsigned channel, bool permit_failure)
{
  union {struct sensor_temp t;struct sensor_baro p;} event;
  size_t size = channel ? sizeof(event.p) : sizeof(event.t);
  uint64_t begin = bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd, &event, size, sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - begin <= 2200000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return 0;
    }

  assert(observed_result == size && bench_now_us() - begin <= 2200000);
  double value = channel ? event.p.pressure : event.t.temperature;
  uint64_t stamp = channel ? event.p.timestamp : event.t.timestamp;
  assert(isfinite(value) && stamp <= bench_now_us());
  if (channel) assert(isnan(event.p.temperature));
  unsigned match = 0, index = 0;
  for (unsigned i = 0; i < 32; i++)
    {
      struct acquisition *a = &history[i];
      if (a->epoch > delivered[channel] && a->epoch > match && (a->eligible & (1u << channel)) &&
          a->at[channel] && stamp >= a->at[channel] && value >= vectors[a->vector].low[channel] &&
          value <= vectors[a->vector].high[channel]) {match = a->epoch; index = a->vector;}
    }
  assert(match > delivered[channel]);
  delivered[channel] = match;
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  printf("BENCHSIM {\"event\":\"sample\",\"channel\":%u,\"epoch\":%u,\"value\":%.8f}\n", channel, match, value);
  return index;
}
static unsigned sample(int fd, unsigned channel)
{
  return sample_observed(fd, channel, false);
}

static void failure(int fd, unsigned channel)
{
  union {struct sensor_temp t;struct sensor_baro p;} event, before;
  size_t size = channel ? sizeof(event.p) : sizeof(event.t);
  memset(&event, 0xa5, sizeof(event)); memcpy(&before, &event, sizeof(event));
  uint64_t begin = bench_now_us();
  assert(read(fd, &event, size) < 0 && !memcmp(&event, &before, sizeof(event)));
  assert(bench_now_us() - begin <= 2200000 && !bus.active && !nxmutex_is_locked(&bus.mutex));
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  if (!strcmp(argv[1], "model/bdu_retention"))
    {
      regs[16] = 0x12; next_conversion = 1000000;
      tick(1000000); assert(valid && holding && epoch == 1);
      input = 1; tick(2000000); assert(frame == 0 && holding);
      read_register(42); assert(!holding);
      tick(3000000); assert(frame == 1 && holding && epoch == 2);
      unsigned value = read_register(43);
      assert(read_register(43) == value); /* DA clear does not erase output. */
      return 0;
    }
  int fds[2];
  for (unsigned i = 0; i < BENCH_VECTOR_COUNT; i++)
    for (unsigned ch = 0; ch < 2; ch++)
      {
        char name[192];
        snprintf(name, sizeof(name), "numeric/%s/%s", vectors[i].name, ch ? "pressure" : "temperature");
        if (!strcmp(argv[1], name))
          {input = i; setup(fds); assert(sample(fds[ch], ch) == i); goto done;}
      }
  clear_first = !strcmp(argv[1], "diagnostic/clear_first");
  setup(fds);
  if (!strcmp(argv[1], "data/stable_change") || clear_first)
    {
      sample(fds[0], 0); sample(fds[1], 1);
      input = 1;
      for (unsigned ch = 0; ch < 2; ch++)
        {
          bool fresh = false;
          for (unsigned attempt = 0; attempt < 3 && !fresh; attempt++) fresh = sample(fds[ch], ch) == 1;
          assert(fresh);
        }
    }
  else if (!strcmp(argv[1], "os/lifecycle"))
    {
      for (unsigned ch = 0; ch < 2; ch++)
        {
          char small[1]; assert(read(fds[ch], small, sizeof(small)) < 0);
          assert(close(fds[ch]) == 0);
          unsigned before = bus.messages;
          bench_sensor_check_disabled(types[ch], 0, ch ? sizeof(struct sensor_baro) : sizeof(struct sensor_temp));
          assert(before == bus.messages);
          sample(fds[1-ch], 1-ch);
          fds[ch] = open(paths[ch], O_RDONLY); assert(fds[ch] >= 0);
          sample(fds[ch], ch);
        }
    }
  else if (!strcmp(argv[1], "integrity/transport_error"))
    {
      bus.next_transfer_error = -EIO; sample_observed(fds[0],0,true); bench_finish_transient(&bus.next_transfer_error);
      sample(fds[0], 0);
    }
  else if (!strcmp(argv[1], "integrity/partial_recovery"))
    {
      fault_register = 44; sample_observed(fds[0], 0, true); assert(fault_hit);
      input = 1;
      sample(fds[0], 0); sample(fds[1], 1);
    }
  else return 65;
done:
  assert(close(fds[0]) == 0 && close(fds[1]) == 0);
  return 0;
}
