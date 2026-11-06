/* DHT20 V1.0 May2021 protocol. Real frozen NuttX owns scheduler, Sensor,
 * memory, locks, file descriptors and timestamps. Model-only recovery bytes
 * are private stimuli; they are never installed into the public target.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/dht20.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "scenario.h"

static struct bench_i2c bus;
static const char *paths[] = {"/dev/uorb/sensor_ambient_temp0", "/dev/uorb/sensor_humi0"};
static const int types[] = {SENSOR_TYPE_AMBIENT_TEMPERATURE, SENSOR_TYPE_RELATIVE_HUMIDITY};
static uint64_t power_ready, allowed_trigger, ready_at, recovery_at, write_start;
static uint32_t conversion_us = 80000;
static unsigned current, latched, epoch, delivered[2], invalid_before;
static unsigned restored, recovery_stage, recovery_index, txpos, rxpos;
static bool calibrated = true, calibration_stuck, stuck, shot, status_seen;
static uint8_t tx[3], frame[7], recovery_reply[3];
static int corrupt_byte = -1, corrupt_bit;
static unsigned corrupt_reads;
static const uint8_t recovery_registers[] = {0x1b, 0x1c, 0x1e};
static const uint8_t recovery_pairs[][2] = {{0xa5,0x13}, {0x27,0xc9}, {0x6d,0xe2}};
struct acquisition {unsigned epoch, vector; uint64_t at;};
static struct acquisition history[64];

static uint8_t crc(const uint8_t *bytes)
{
  uint64_t p = 0;
  for (unsigned i=0; i<6; i++) p = (p << 8) | bytes[i];
  p = (p << 8) ^ (0xffull << 48);
  for (int shift=47; shift>=0; shift--)
    if (p & (1ull << (shift+8))) p ^= 0x131ull << shift;
  return p;
}

static bool busy(void) {return shot && (stuck || bench_now_us() < ready_at);}
static uint8_t status(void)
{
  return (calibrated ? 0x18 : 0x08) | (vectors[current].bytes[0] & 4) | (busy() ? 0x80 : 0);
}

static int command(const uint8_t *data, uint64_t start, uint64_t now)
{
  if (data[0] == 0xac)
    {
      assert(data[1] == 0x33 && data[2] == 0 && calibrated);
      assert(start >= allowed_trigger);
      if (busy()) bench_model_unsupported("dht20_overlapping_conversion");
      epoch++; shot = true; ready_at = now + conversion_us; latched = current;
      return 0;
    }
  for (unsigned i=0; i<3; i++)
    {
      if (data[0] == recovery_registers[i])
        {
          assert(data[1] == 0 && data[2] == 0);
          recovery_stage = 1; recovery_index = i; recovery_at = now + 5000;
          return 0;
        }
      if (data[0] == (0xb0 | recovery_registers[i]))
        {
          assert(recovery_stage == 2 && i == recovery_index);
          if (start < recovery_at) bench_model_unsupported("dht20_vendor_approximate_restore_delay");
          assert(data[1] == recovery_pairs[i][0] && data[2] == recovery_pairs[i][1]);
          restored |= 1u << i; recovery_stage = 0;
          if (restored == 7) {calibrated = !calibration_stuck; allowed_trigger = now + 10000;}
          return 0;
        }
    }
  bench_model_unsupported("dht20_command");
  return -ENOTSUP;
}

static int address(void *context, uint16_t addr, bool reading)
{
  assert(addr == BENCH_I2C_ADDRESS);
  if (bench_now_us() < power_ready) return -ENXIO;
  if (!reading) write_start = bench_now_us();
  return 0;
}

static int message(void *context, struct i2c_msg_s *msg, bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && !(msg->flags & I2C_M_TEN));
  uint64_t now = bench_now_us();
  if (!(msg->flags & I2C_M_READ))
    {
      if (start) txpos = 0;
      if (txpos + msg->length > 3) bench_model_unsupported("dht20_command_length");
      memcpy(tx + txpos, msg->buffer, msg->length); txpos += msg->length;
      assert(!(txpos == 1 && tx[0] == 0x71)); /* I2C read address is not payload. */
      if (txpos == 3) return command(tx, write_start, now);
      assert(!stop);
      return 0;
    }
  if (start)
    {
      rxpos = 0;
      if (!status_seen) {status_seen = true; if (calibrated) allowed_trigger = now + 10000;}
      if (recovery_stage)
        {
          if (recovery_stage != 1) bench_model_unsupported("dht20_recovery_read_sequence");
          if (now < recovery_at) bench_model_unsupported("dht20_vendor_approximate_read_delay");
          recovery_reply[0] = status();
          memcpy(recovery_reply+1,recovery_pairs[recovery_index],2);
        }
      else
        {
          memcpy(frame, vectors[latched].bytes, sizeof(frame));
          frame[0] = status(); frame[6] = crc(frame);
          if (corrupt_byte >= 0) frame[corrupt_byte] ^= 1u << corrupt_bit;
        }
    }
  if (recovery_stage)
    {
      if (rxpos + msg->length > 3) bench_model_unsupported("dht20_recovery_read_length");
      memcpy(msg->buffer,recovery_reply+rxpos,msg->length);rxpos+=msg->length;
      if (rxpos == 3) {recovery_stage=2;recovery_at=now+9ull*msg->length*1000000/msg->frequency+10000;}
      return 0;
    }
  if (rxpos + msg->length > 7) bench_model_unsupported("dht20_frame_length");
  memcpy(msg->buffer,frame+rxpos,msg->length);rxpos+=msg->length;
  if (corrupt_byte >= 0 && rxpos > (unsigned)corrupt_byte) corrupt_reads++;
  if (rxpos == 7 && shot && !busy() && calibrated && !(frame[0] & 0x80) && crc(frame) == frame[6])
    {
      struct acquisition *a=&history[epoch%64];
      if (a->epoch != epoch) *a=(struct acquisition){epoch,latched,now+9ull*msg->length*1000000/msg->frequency};
    }
  else if (rxpos == 7 && corrupt_byte >= 0) invalid_before = epoch + 1;
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
static const struct bench_i2c_device device = {.message=message,.address=address, .transfer_failed=transfer_failed};

static void setup(int *fds)
{
  bench_i2c_init(&bus,&device,NULL,100000);
  power_ready=bench_now_us()+100000;allowed_trigger=power_ready+10000;
  uint64_t begin=bench_now_us();
  assert(dht20_register(&bus.dev,BENCH_I2C_ADDRESS,0)==0);
  assert(bench_now_us()-begin<=600000 && calibrated && epoch==0);
  for(unsigned i=0;i<2;i++){fds[i]=open(paths[i],O_RDONLY);assert(fds[i]>=0);}
}

static unsigned sample_observed(int fd,unsigned channel,unsigned index, bool permit_failure)
{
  union {struct sensor_temp t;struct sensor_humi h;} event;
  size_t size=channel ? sizeof(event.h):sizeof(event.t);
  current=index;uint64_t begin=bench_now_us();memset(&event,0xa5,sizeof(event));
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd,&event,size, sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - begin <= 250000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return 0;
    }

  assert(observed_result==size && bench_now_us()-begin<=250000);
  double value=channel ? event.h.humidity:event.t.temperature;
  uint64_t stamp=channel ? event.h.timestamp:event.t.timestamp;
  unsigned match=0,vector=0;
  for(unsigned i=0;i<64;i++)
    {
      struct acquisition *a=&history[i];
      if(a->epoch<=delivered[channel] || a->epoch<invalid_before || a->epoch<=match)continue;
      double expected=vectors[a->vector].expected[channel];
      double tolerance=0.00001+2*FLT_EPSILON*fmax(1,fabs(expected));
      if(stamp>=a->at && stamp<=bench_now_us() && isfinite(value) && fabs(value-expected)<=tolerance)
        {match=a->epoch;vector=a->vector;}
    }
  assert(match>delivered[channel]);delivered[channel]=match;
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  printf("BENCHSIM {\"event\":\"sample\",\"channel\":%u,\"epoch\":%u,\"value\":%.8f}\n",channel,match,value);
  return vector;
}
static unsigned sample(int fd,unsigned channel,unsigned index)
{
  return sample_observed(fd, channel, index, false);
}

static void failure(int fd,unsigned channel)
{
  union {struct sensor_temp t;struct sensor_humi h;} event,before;
  size_t size=channel ? sizeof(event.h):sizeof(event.t);
  memset(&event,0xa5,sizeof(event));memcpy(&before,&event,sizeof(event));
  uint64_t start=bench_now_us();
  int result=read(fd,&event,size);
  printf("BENCHSIM {\"event\":\"failure_observation\",\"channel\":%u,\"return\":%d,\"errno\":%d,\"elapsed_us\":%llu,\"bus_active\":%d,\"bus_locked\":%d}\n",
         channel,result,errno,(unsigned long long)(bench_now_us()-start),bus.active,nxmutex_is_locked(&bus.mutex));
  assert(result<0 && !memcmp(&event,&before,sizeof(event)));
  assert(bench_now_us()-start<=250000 && !bus.active && !nxmutex_is_locked(&bus.mutex));
}

int bench_case_main(int argc,char **argv)
{
  if(argc!=2)return 64;
  const char *name=argv[1];int fds[2];
  if(!strcmp(name,"model/protocol"))
    {
      for(unsigned i=0;i<BENCH_VECTOR_COUNT;i++)assert(crc(vectors[i].bytes)==vectors[i].bytes[6]);
      uint8_t begin[]={0x1c,0,0},restore[]={0xbc,0x27,0xc9};
      calibrated=false;assert(command(begin,0,0)==0 && recovery_stage==1 && recovery_at==5000);
      recovery_stage=2;recovery_at=15000;
      assert(command(restore,15000,15270)==0 && restored==2);
      calibrated=true;allowed_trigger=20000;
      uint8_t trigger[]={0xac,0x33,0};
      assert(command(trigger,20000,20270)==0 && ready_at==100270 && epoch==1);
      return 0;
    }
  if(!strcmp(name,"initialization/uncalibrated"))
    {
      calibrated=false;calibration_stuck=true;
      bench_i2c_init(&bus,&device,NULL,100000);power_ready=bench_now_us()+100000;
      uint64_t begin=bench_now_us();assert(dht20_register(&bus.dev,BENCH_I2C_ADDRESS,0)<0);
      assert(bench_now_us()-begin<=600000);
      for(unsigned i=0;i<2;i++)assert(bench_sensor_find(types[i],0)==NULL);
      return 0;
    }
  if(!strcmp(name,"initialization/recovery"))calibrated=false;
  if(!strncmp(name,"timing/conversion_",18))conversion_us=atoi(name+18);
  setup(fds);
  for(unsigned i=0;i<BENCH_VECTOR_COUNT;i++)if(!strcmp(name,vectors[i].id))
    {sample(fds[0],0,i);sample(fds[1],1,i);goto done;}
  if(!strcmp(name,"data/stable_change"))
    {
      sample(fds[0],0,0);sample(fds[1],1,0);
      uint64_t end=bench_now_us()+2000000;
      for(unsigned ch=0;ch<2;ch++){bool seen=false;while(bench_now_us()<end && !seen)seen=sample(fds[ch],ch,1)==1;assert(seen);}
    }
  else if(!strcmp(name,"os/lifecycle"))
    {
      for(unsigned i=0;i<2;i++)
        {
          char small[1];assert(read(fds[i],small,1)<0);assert(close(fds[i])==0);
          unsigned messages=bus.messages;
          bench_sensor_check_disabled(types[i],0,i?sizeof(struct sensor_humi):sizeof(struct sensor_temp));
          assert(messages==bus.messages);sample(fds[1-i],1-i,1);
          fds[i]=open(paths[i],O_RDONLY);assert(fds[i]>=0);sample(fds[i],i,0);
        }
    }
  else if(!strcmp(name,"initialization/recovery"))
    {assert(restored==7);sample(fds[0],0,0);sample(fds[1],1,0);}
  else if(!strncmp(name,"timing/conversion_",18))
    {sample(fds[0],0,0);sample(fds[1],1,0);}
  else if(!strncmp(name,"integrity/crc_",14))
    {
      assert(sscanf(name+14,"%d_%d",&corrupt_byte,&corrupt_bit)==2);
      assert(corrupt_byte>=0 && corrupt_byte<7 && corrupt_bit>=0 && corrupt_bit<8);
      failure(fds[0],0);failure(fds[1],1);assert(corrupt_reads>0);
    }
  else if(!strcmp(name,"integrity/transport_error"))
    {
      sample(fds[0],0,0); /* Arming a future fault does not invalidate data; the actual failure callback does. */
      for(unsigned i=0;i<2;i++){bus.next_transfer_error=-EIO;sample_observed(fds[i],i,0,true);bench_finish_transient(&bus.next_transfer_error);}
      sample(fds[0],0,1);sample(fds[1],1,1);
    }
  else if(!strcmp(name,"device/permanent_busy"))
    {stuck=true;failure(fds[0],0);failure(fds[1],1);}
  else return 65;
done:
  assert(close(fds[0])==0 && close(fds[1])==0);return 0;
}
