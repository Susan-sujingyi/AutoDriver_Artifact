/* Infineon DPS310 V1.2: command conversions, independent T/P data and
 * signed coefficient/raw frames. The public NuttX product policy supplies
 * freshness/cache/deadline rules; the OS itself supplies all native APIs.
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
#include <nuttx/sensors/dps310.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "vectors.h"
#include "scenario.h"

struct acquisition {unsigned epoch, vector, mask; uint64_t completed, acquired; bool valid;};
static struct acquisition frames[2], acquired[2];
static struct bench_i2c bus;
static uint8_t regs[128], pointer, trim[18], factory_source;
static unsigned input, pending_input, active, ready, epochs, write_offset;
static uint64_t powered, due, conversion = 14800, last_stamp[2];
static bool stuck, fault_hit;
static int fault_register = -1;
static const char *paths[] = {"/dev/uorb/sensor_ambient_temp0", "/dev/uorb/sensor_baro0"};
static const char *channels[] = {"temperature", "pressure"};
static const int types[] = {SENSOR_TYPE_AMBIENT_TEMPERATURE, SENSOR_TYPE_BAROMETER};
union sample_event {struct sensor_temp t; struct sensor_baro p;};

static void tick(uint64_t now)
{
  if (active && !stuck && now >= due)
    {
      unsigned ch = active == 1 ? 1 : 0;
      frames[ch] = (struct acquisition){.epoch = ++epochs, .vector = pending_input, .completed = due};
      ready |= 1u << ch;
      active = 0;
    }
}
static void configuration(void)
{
  assert((regs[6] & 15) == 3 && (regs[7] & 15) == 3);
  assert((regs[7] & 128) == factory_source);
  assert(!(regs[9] & 0x7f) && !(regs[12] & 0x80));
}
static void write_register(unsigned reg, uint8_t value)
{
  assert(reg < 128);
  if (reg == 12)
    {
      if ((value & 15) == 9)
        {
          memset(regs, 0, sizeof(regs));
          memset(frames, 0, sizeof(frames)); memset(acquired, 0, sizeof(acquired));
          powered = bench_now_us(); active = ready = 0;
        }
      else if (value != 0x80) bench_model_unsupported("dps310_reset_command");
      return;
    }
  if (reg == 8)
    {
      assert(value <= 2);
      if (active && value) bench_model_unsupported("dps310_overlapping_command");
      if (value)
        {
          configuration(); active = value; pending_input = input;
          due = bench_now_us() + conversion;
          ready &= ~(value == 2 ? 1u : 2u);
        }
      else active = 0;
      return;
    }
  /* Manufacturer private correction is observed, not asserted as required:
   * no evidenced silicon identity or missing-correction transfer function. */
  if (reg == 0x0e || reg == 0x0f || reg == 0x62)
    {regs[reg] = value; printf("BENCHSIM correction_write reg=%u value=%u\n", reg, value); return;}
  if (reg == 6 || reg == 7 || reg == 9) {regs[reg] = value; return;}
  bench_model_unsupported("dps310_register_write_not_modeled");
}
static uint8_t read_register(unsigned reg)
{
  assert(reg < 128);
  if (reg == 13) return 0x10;
  if (reg == 8)
    return active | ((ready & 1) ? 0x20 : 0) | ((ready & 2) ? 0x10 : 0) |
           (bench_now_us() >= powered + 12000 ? 0x40 : 0) | (bench_now_us() >= powered + 40000 ? 0x80 : 0);
  if (reg >= 0x10 && reg <= 0x21)
    {
      if (bench_now_us() < powered + 40000) bench_model_unsupported("dps310_calibration_before_ready");
      return trim[reg - 0x10];
    }
  if (reg == 0x28) return factory_source | 0x57;
  if (reg < 6)
    {
      unsigned ch = reg >= 3 ? 0 : 1;
      struct acquisition *a = &frames[ch];
      if (!a->epoch) return 0;
      a->mask |= 1u << (reg % 3);
      if (a->mask == 7)
        {
          if (!a->acquired) a->acquired = bench_now_us();
          a->valid = true; acquired[ch] = *a;
        }
      else if (!ch) acquired[0].valid = false;
      /* The datasheet does not fix which byte clears RDY. This model clears
       * on the last channel register, retaining output data afterwards. */
      if (reg % 3 == 2) ready &= ~(1u << ch);
      return vectors[a->vector].raw[reg];
    }
  if (reg == 6 || reg == 7 || reg == 9 || reg == 12 || reg == 14 || reg == 15 || reg == 0x62) return regs[reg];
  bench_model_unsupported("dps310_register_read_not_modeled");
}
static int address(void *context, uint16_t addr, bool reading)
{
  assert(addr == BENCH_I2C_ADDRESS);
  return bench_now_us() < powered + 2500 ? -ENXIO : 0;
}
static int message(void *context, struct i2c_msg_s *msg, bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && !(msg->flags & I2C_M_TEN) && msg->frequency == 100000);
  tick(bench_now_us());
  if (!(msg->flags & I2C_M_READ))
    {
      if (start) write_offset = 0;
      for (unsigned i = 0; i < msg->length; i++)
        {
          if (!write_offset++) pointer = msg->buffer[i];
          else write_register(pointer++, msg->buffer[i]);
        }
    }
  return 0;
}
static int read_byte(void *context, const struct i2c_msg_s *msg, unsigned index, uint8_t *value)
{
  tick(bench_now_us());
  if (pointer == fault_register)
    {
      fault_register = -1; fault_hit = true;
      if (pointer >= 3 && pointer <= 5)
        {acquired[0].valid = false; frames[0].mask = 0; frames[0].acquired = 0;}
      return -EIO;
    }
  *value = read_register(pointer++);
  return 0;
}
/* Scope invalidation to the requested acquisition: pressure-only errors
 * do not invalidate an independently valid temperature cache. */
static int requested_channel = -1;
static void transfer_failed(void *context, const struct i2c_msg_s *messages,
                            int count, int error)
{
  assert(error < 0);
  if (requested_channel >= 0)
    acquired[requested_channel].valid = false;
}
static const struct bench_i2c_device device = {.message = message, .read_byte = read_byte, .address = address, .transfer_failed=transfer_failed};
static size_t event_size(unsigned ch) {return ch ? sizeof(struct sensor_baro) : sizeof(struct sensor_temp);}
static void idle(void) {assert(!bus.active && !nxmutex_is_locked(&bus.mutex));}
static void prepare(void)
{
  memcpy(trim, vectors[input].cal, sizeof(trim));
  powered = bench_now_us();
  bench_i2c_init(&bus, &device, NULL, 100000);
}
static void setup(int *fds)
{
  uint64_t begin = bench_now_us();
  assert(dps310_register(&bus.dev, BENCH_I2C_ADDRESS, 0) == 0);
  assert(bench_now_us() - begin <= 100000);
  configuration();
  for (unsigned ch = 0; ch < 2; ch++) {fds[ch] = open(paths[ch], O_RDONLY); assert(fds[ch] >= 0);}
  idle();
}
static unsigned sample_observed(int fd, unsigned ch, bool permit_failure)
{
  union sample_event event;
  memset(&event, 0xa5, sizeof(event));
  uint64_t begin = bench_now_us();
  memset(&event, 0xa5, sizeof(event));

  requested_channel = ch;
  ssize_t result = bench_observe_read(fd, &event, event_size(ch), sizeof(event), permit_failure);
  requested_channel = -1;
  if (result < 0)
    {
      assert(bench_now_us() - begin <= 100000);
      idle();
      return 0;
    }

  uint64_t end = bench_now_us();
  printf("BENCHSIM fetch channel=%u result=%ld errno=%d elapsed_us=%llu\n",ch,(long)result,errno,(unsigned long long)(end-begin));
  assert(result == event_size(ch) && end - begin <= 100000);
  const struct acquisition *a = &acquired[ch], *t = &acquired[0];
  uint64_t stamp = ch ? event.p.timestamp : event.t.timestamp;
  double value = ch ? event.p.pressure : event.t.temperature;
  assert(a->valid && a->mask == 7 && a->completed > begin && a->acquired <= stamp);
  assert(isfinite(value) && stamp > last_stamp[ch] && stamp <= end);
  long double expected = vectors[a->vector].expected[0];
  if (ch)
    {
      assert(isnan(event.p.temperature));
      assert(t->valid && t->mask == 7 && t->acquired <= stamp);
      uint64_t age = a->completed > t->completed ? a->completed - t->completed : t->completed - a->completed;
      assert(age <= 1000000);
      expected = mixed_pressure[a->vector][t->vector];
    }
  long double tolerance = (ch ? 1.0L : 0.01L) + 2*FLT_EPSILON*fmaxl(1, fabsl(expected));
  assert(fabsl(value - expected) <= tolerance);
  last_stamp[ch] = stamp; idle();
  printf("BENCHSIM sample channel=%u epoch=%u value=%.9f\n", ch, a->epoch, value);
  return a->vector;
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
  printf("BENCHSIM negative_fetch result=%ld errno=%d elapsed_us=%llu\n",(long)result,errno,(unsigned long long)(bench_now_us()-begin));
  assert(result < 0 && !memcmp(&event, &before, sizeof(event)));
  assert(bench_now_us() - begin <= 100000); idle();
}
static void model_contract(void)
{
  regs[6] = regs[7] = 3;
  write_register(8, 2); bench_wire_delay(14800, 1000000); tick(bench_now_us());
  read_register(3); read_register(4); read_register(5);
  assert(acquired[0].valid && acquired[0].vector == 0);
  input = 1;
  write_register(8, 2); bench_wire_delay(14800, 1000000); tick(bench_now_us());
  assert(acquired[0].valid && acquired[0].vector == 0 && frames[0].vector == 1);
  read_register(3); assert(!acquired[0].valid);
  read_register(5); read_register(4); assert(acquired[0].valid && acquired[0].vector == 1);
}
int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  const char *name = argv[1]; int fds[2];
  for (unsigned i = 0; i < VECTOR_COUNT; i++)
    for (unsigned ch = 0; ch < 2; ch++)
      for (unsigned src = 0; src < 2; src++)
        {
          char expected[192]; snprintf(expected,sizeof(expected),"numeric/%s/%s/source%u",vectors[i].id,channels[ch],src);
          if (!strcmp(name,expected))
            {input=i;factory_source=src?128:0;prepare();setup(fds);assert(sample(fds[ch],ch)==i);goto done;}
        }
  prepare();
  if (!strcmp(name,"model/temperature_witness")) {model_contract();return 0;}
  if (!strncmp(name,"os/registration_collision_",26))
    {
      unsigned ch=name[26]-'0';assert(ch<2);
      static const struct sensor_ops_s ops={0};
      struct sensor_lowerhalf_s dummy={.type=types[ch],.ops=&ops};
      assert(sensor_register(&dummy,0)==0);
      uint64_t begin=bench_now_us();
      assert(dps310_register(&bus.dev,BENCH_I2C_ADDRESS,0)<0 && bench_now_us()-begin<=100000);
      assert(bench_sensor_find(types[ch],0)==&dummy && !bench_sensor_find(types[1-ch],0));
      sensor_unregister(&dummy,0);setup(fds);sample(fds[1],1);goto done;
    }
  setup(fds);
  if (!strcmp(name,"data/stable_change"))
    {
      sample(fds[0],0);sample(fds[1],1);
      input=1;sample(fds[0],0);assert(sample(fds[1],1)==1);
      bench_wire_delay(1100000,1000000);input=2;assert(sample(fds[1],1)==2);
    }
  else if (!strcmp(name,"data/slow_conversion"))
    {conversion=30000;sample(fds[1],1);sample(fds[0],0);sample(fds[1],1);}
  else if (!strcmp(name,"os/lifecycle"))
    {
      for(unsigned ch=0;ch<2;ch++)
        {
          char small[1];assert(read(fds[ch],small,sizeof(small))<0);
          assert(close(fds[ch])==0);unsigned before=bus.messages;
          bench_sensor_check_disabled(types[ch],0,event_size(ch));assert(before==bus.messages);
          sample(fds[1-ch],1-ch);fds[ch]=open(paths[ch],O_RDONLY);assert(fds[ch]>=0);sample(fds[ch],ch);
        }
    }
  else if (!strcmp(name,"integrity/transport_error"))
    {bus.next_transfer_error=-EIO;sample_observed(fds[0],0,true);bench_finish_transient(&bus.next_transfer_error);sample(fds[1],1);}
  else if (!strcmp(name,"integrity/temperature_cache_after_error"))
    {sample(fds[0],0);fault_register=4;sample_observed(fds[0],0,true);assert(fault_hit);input=1;sample(fds[1],1);}
  else if (!strncmp(name,"integrity/partial_read_",23))
    {
      fault_register=name[23]-'0';assert(fault_register>=0 && fault_register<6);
      unsigned ch=fault_register<3?1:0;
      sample_observed(fds[ch],ch,true);assert(fault_hit);input=1;sample(fds[ch],ch);sample(fds[1-ch],1-ch);
    }
  else if (!strcmp(name,"diagnostic/permanent_busy")) {stuck=true;failure(fds[0],0);}
  else return 65;
done:
  assert(close(fds[0])==0 && close(fds[1])==0);idle();return 0;
}
