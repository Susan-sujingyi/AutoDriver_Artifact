/* Bosch DS001-05 Rev1.4: power transitions, 10Hz frames, STOP shadow release.
 * Public native factory/fetch policy, real frozen NuttX Sensor and scheduler.
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
#include <nuttx/sensors/bmm150.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "vectors.h"
#include "scenario.h"

struct frame {unsigned epoch, vector; bool valid, ready; uint8_t bytes[8];};
struct witness {unsigned epoch, vector, mask; bool valid; uint64_t acquired;};
static struct frame output, shadow;
static struct witness history[64];
static struct bench_i2c bus;
static uint8_t regs[128], trim[21], pointer;
static unsigned input, epochs, delivered, write_offset;
static uint64_t wake_ready, next_conversion, last_stamp;
static unsigned first_delay = 9785, period = 100000;
static bool powered, shadowed, read_stop, stall, fault_hit, overrun;
static int invalid, fault_offset = -1;
static const char *path = "/dev/uorb/sensor_mag0";

static void fields(void)
{
  assert(powered && regs[0x4c] == 0 && regs[0x51] == 4 && regs[0x52] == 14);
  assert(!(regs[0x4e] & 0x38)); /* Axes enabled; unwired interrupt policy is free. */
}
static void tick(uint64_t now)
{
  if (next_conversion && !stall && now >= next_conversion)
    {
      unsigned count = (now - next_conversion) / period + 1;
      next_conversion += (uint64_t)count * period; epochs += count;
      if((regs[0x4d]&0x80) && (output.ready || count>1))overrun=true;
      output = (struct frame){.epoch=epochs, .vector=input, .valid=invalid==0, .ready=true};
      memcpy(output.bytes, vectors[input].wire, 8);
      if (invalid >= 1 && invalid <= 3)
        {output.bytes[2*(invalid-1)]=0; output.bytes[2*(invalid-1)+1]=0x80;}
      if (invalid == 4) {output.bytes[6]=0; output.bytes[7]=0;}
      if (invalid == 5) {output.bytes[6]=0x40; output.bytes[7]=0x9c;}
      if (invalid == 6)
        {output.bytes[4]=0xfe;output.bytes[5]=0x7f;output.bytes[6]=0x3c;output.bytes[7]=0x9c;}
    }
}
static void reset_registers(void)
{
  memset(regs+0x4c,0,7); regs[0x4c]=6; regs[0x4d]=0x3f; regs[0x4e]=7;
  next_conversion=0; shadowed=overrun=false; output=(struct frame){0};
  memset(history,0,sizeof(history));
}
static void write_register(unsigned reg,uint8_t value)
{
  assert(reg<128);
  uint64_t now=bench_now_us(); tick(now);
  if(reg==0x4b)
    {
      if(!(value&1)) {powered=false;reset_registers();}
      else if(!powered) {powered=true;reset_registers();wake_ready=now+3000;}
      else if((value&0x82)==0x82) {reset_registers();wake_ready=now+1000;}
      regs[reg]=value&5;return;
    }
  if(!powered)return;
  if(now<wake_ready)bench_model_unsupported("bmm150_write_during_wakeup_reset");
  if(reg>=0x40 && reg<=0x4a)return; /* Read-only output/status. */
  if(reg<0x4c || reg>0x52)bench_model_unsupported("bmm150_register_write_not_modeled");
  uint8_t before=regs[reg];regs[reg]=value;
  if(reg==0x4c)
    {
      assert((value&6)==0 || (value&6)==6);
      if((value&6)==0 && ((before&6)!=0 || !next_conversion)) next_conversion=now+first_delay;
      else if((value&6)==6)next_conversion=0;
    }
}
static uint8_t read_register(unsigned reg)
{
  assert(reg<128);
  if(reg==0x4b)return regs[reg];
  if(!powered)return 0;
  if(bench_now_us()<wake_ready)bench_model_unsupported("bmm150_read_during_wakeup_reset");
  if(reg==0x40)return 0x32;
  if(reg>=0x5d && reg<=0x71)return trim[reg-0x5d];
  if(reg>=0x42 && reg<=0x49)
    {
      if(!shadowed){shadow=output;shadowed=true;}
      unsigned byte=reg-0x42;
      if(shadow.epoch)
        {
          struct witness *w=&history[shadow.epoch%64];
          if(w->epoch!=shadow.epoch)*w=(struct witness){.epoch=shadow.epoch,.vector=shadow.vector,.valid=shadow.valid};
          w->mask|=1u<<byte;
          if(w->mask==255 && !w->acquired)w->acquired=bench_now_us();
        }
      return byte==6?(shadow.bytes[6]&~1u)|(shadow.ready?1:0):shadow.bytes[byte];
    }
  if(reg==0x4a)
    {
      if((regs[0x4d]&0x3f)!=0x3f)bench_model_unsupported("bmm150_threshold_interrupt_status");
      bool overflow=false;
      for(unsigned ch=0;ch<3;ch++)
        {
          unsigned word=output.bytes[2*ch]|((unsigned)output.bytes[2*ch+1]<<8);
          overflow |= ch==2 ? (word>>1)==0x4000 : (word>>3)==0x1000;
        }
      uint8_t value=(overrun?0x80:0)|(overflow?0x40:0);
      overrun=false;return value;
    }
  if(reg>=0x4c && reg<=0x52)return regs[reg];
  bench_model_unsupported("bmm150_register_read_not_modeled");
}
static void stop_read(void)
{
  /* A newer shadow-register update becomes visible at STOP with new DRDY. */
  if(shadowed && output.epoch==shadow.epoch)output.ready=false;
  shadowed=false;
}
static int message(void *context,struct i2c_msg_s *msg,bool start,bool repeated,bool stop)
{
  assert(msg->addr==BENCH_I2C_ADDRESS && !(msg->flags&I2C_M_TEN) && msg->frequency==100000);
  tick(bench_now_us());
  if(msg->flags&I2C_M_READ)read_stop=stop;
  else
    {
      if(start)write_offset=0;
      for(unsigned i=0;i<msg->length;i++)
        {
          if(!write_offset++)pointer=msg->buffer[i]&127;
          else {write_register(pointer,msg->buffer[i]);pointer=(pointer+1)&127;}
        }
      if(stop)stop_read();
    }
  return 0;
}
static int read_byte(void *context,const struct i2c_msg_s *msg,unsigned index,uint8_t *value)
{
  tick(bench_now_us());
  /* Inject by physical byte address, independent of the candidate's burst
   * boundaries. The Hall/DRDY byte may also be read as a status probe. */
  if(pointer>=0x42 && pointer<=0x49 && (int)(pointer-0x42)==fault_offset)
    {
      fault_offset=-1;fault_hit=true;
      unsigned epoch=shadowed?shadow.epoch:output.epoch;
      history[epoch%64].mask=0;history[epoch%64].acquired=0;
      stop_read();return -EIO;
    }
  *value=read_register(pointer);pointer=(pointer+1)&127;
  if(read_stop && index+1==msg->length)stop_read();
  return 0;
}
static const struct bench_i2c_device device={.message=message,.read_byte=read_byte};
static void idle(void){assert(!bus.active && !nxmutex_is_locked(&bus.mutex));}
static void prepare(void)
{
  reset_registers();memcpy(trim,vectors[input].trim,21);
  bench_i2c_init(&bus,&device,NULL,100000);
}
static int activate_node(void)
{int fd=open(path,O_RDONLY);assert(fd>=0);return fd;}
static int setup(void)
{
  uint64_t begin=bench_now_us();
  assert(bmm150_register(&bus.dev,BENCH_I2C_ADDRESS,0)==0);
  assert(bench_now_us()-begin<=100000);fields();idle();return activate_node();
}
static bool matches(float value,long double expected)
{return isfinite(value) && fabsl(value-expected)<=0.125L+2*FLT_EPSILON*fmaxl(1,fabsl(expected));}
static unsigned sample_observed(int fd, bool permit_failure)
{
  struct sensor_mag event;
  uint64_t begin=bench_now_us();
  memset(&event, 0xa5, sizeof(event));
ssize_t result=bench_observe_read(fd,&event,sizeof(event), sizeof(event), permit_failure);
  if (result < 0)
    {
      assert(bench_now_us() - begin <= 250000);
      idle();
      return 0;
    }

  printf("BENCHSIM fetch result=%ld errno=%d elapsed_us=%llu\n",(long)result,errno,(unsigned long long)(bench_now_us()-begin));
  assert(result==sizeof(event) && bench_now_us()-begin<=250000);
  assert(isnan(event.temperature) && event.status==0 && event.timestamp>last_stamp && event.timestamp<=bench_now_us());
  unsigned match=0,vector=0;
  for(unsigned i=0;i<64;i++)
    {
      struct witness *w=&history[i];
      if(w->epoch>delivered && w->epoch>match && w->mask==255 && w->valid && w->acquired<=event.timestamp &&
         matches(event.x,vectors[w->vector].ut[0]) && matches(event.y,vectors[w->vector].ut[1]) && matches(event.z,vectors[w->vector].ut[2]))
        {match=w->epoch;vector=w->vector;}
    }
  assert(match>delivered);delivered=match;last_stamp=event.timestamp;fields();idle();
  printf("BENCHSIM sample epoch=%u x=%.9f y=%.9f z=%.9f\n",match,event.x,event.y,event.z);
  return vector;
}
static unsigned sample(int fd)
{
  return sample_observed(fd, false);
}
static void failure(int fd)
{
  struct sensor_mag event,before;memset(&event,0xa5,sizeof(event));memcpy(&before,&event,sizeof(event));
  uint64_t begin=bench_now_us();ssize_t result=read(fd,&event,sizeof(event));
  printf("BENCHSIM negative_fetch result=%ld errno=%d elapsed_us=%llu\n",(long)result,errno,(unsigned long long)(bench_now_us()-begin));
  assert(result<0 && !memcmp(&event,&before,sizeof(event)));
  assert(bench_now_us()-begin<=250000);idle();
}
static void model_contract(void)
{
  powered=true;regs[0x4c]=0;regs[0x4d]|=0x80;regs[0x51]=4;regs[0x52]=14;next_conversion=bench_now_us()+first_delay;
  bench_wire_delay(first_delay,1000000);tick(bench_now_us());
  assert(output.ready && output.epoch);
  unsigned first=output.epoch;
  read_register(0x42);input=1;
  bench_wire_delay(period,1000000);tick(bench_now_us());
  assert(output.epoch>first && shadow.epoch==first);
  assert(read_register(0x4a)==0x80 && read_register(0x4a)==0);
  assert(read_register(0x43)==vectors[0].wire[1]);
  stop_read();assert(output.ready);
  assert(read_register(0x43)==vectors[1].wire[1]);
  stop_read();assert(!output.ready);
  assert(read_register(0x43)==vectors[1].wire[1]); /* DRDY clear retains valid data. */
  stop_read();
}
int bench_case_main(int argc,char **argv)
{
  if(argc!=2)return 64;
  const char *name=argv[1];int fd;
  for(unsigned i=0;i<VECTOR_COUNT;i++)
    {
      char expected[192];snprintf(expected,sizeof(expected),"numeric/%s",vectors[i].id);
      if(!strcmp(name,expected))
        {input=i;prepare();fd=setup();assert(sample(fd)==i);assert(sample(fd)==i);goto done;}
    }
  if(!strncmp(name,"integrity/invalid_",18))
    {invalid=name[18]-'0';assert(invalid>=1 && invalid<=6);if(invalid>=5)input=BENCH_SPECIAL_INDEX;}
  if(!strcmp(name,"data/slow_phase")){first_delay=108696;period=108696;}
  prepare();
  if(!strcmp(name,"model/shadow_and_ready")){model_contract();return 0;}
  if(!strcmp(name,"os/invalid_factory"))
    {
      assert(bmm150_register(NULL,BENCH_I2C_ADDRESS,0)<0);
      assert(bmm150_register(&bus.dev,0x10,0)<0);
      assert(bmm150_register(&bus.dev,BENCH_I2C_ADDRESS,-1)<0);
      assert(!bench_sensor_find(SENSOR_TYPE_MAGNETIC_FIELD,0));idle();return 0;
    }
  if(!strcmp(name,"os/registration_collision"))
    {
      static const struct sensor_ops_s ops={0};
      struct sensor_lowerhalf_s dummy={.type=SENSOR_TYPE_MAGNETIC_FIELD,.ops=&ops};
      assert(sensor_register(&dummy,0)==0);uint64_t begin=bench_now_us();
      assert(bmm150_register(&bus.dev,BENCH_I2C_ADDRESS,0)<0 && bench_now_us()-begin<=100000);
      assert(bench_sensor_find(SENSOR_TYPE_MAGNETIC_FIELD,0)==&dummy);
      sensor_unregister(&dummy,0);fd=setup();sample(fd);goto done;
    }
  if(!strncmp(name,"integrity/trim_",15))
    {
      unsigned i=name[15]-'0';assert(i<4);
      unsigned offset=(unsigned[]){11,13,15,15}[i];trim[offset]=0;trim[offset+1]=i==3?0x80:0;
      uint64_t begin=bench_now_us();int ret=bmm150_register(&bus.dev,BENCH_I2C_ADDRESS,0);
      assert(bench_now_us()-begin<=100000);
      if(ret<0){assert(!bench_sensor_find(SENSOR_TYPE_MAGNETIC_FIELD,0));idle();return 0;}
      fd=activate_node();failure(fd);goto done; /* Lazy trim validation is allowed. */
    }
  fd=setup();
  if(!strcmp(name,"data/stable_change"))
    {
      sample(fd);input=1;bench_wire_delay(period,1000000);assert(sample(fd)==1);
      input=2;bench_wire_delay(period,1000000);assert(sample(fd)==2);
    }
  else if(!strcmp(name,"data/already_ready"))
    {bench_wire_delay(period*2,1000000);tick(bench_now_us());assert(output.ready);sample(fd);sample(fd);}
  else if(!strcmp(name,"data/slow_phase")) {sample(fd);sample(fd);sample(fd);}
  else if(!strcmp(name,"os/lifecycle"))
    {
      char small[1];assert(read(fd,small,sizeof(small))<0);assert(close(fd)==0);
      unsigned before=bus.messages;bench_sensor_check_disabled(SENSOR_TYPE_MAGNETIC_FIELD,0,sizeof(struct sensor_mag));
      assert(before==bus.messages);fd=activate_node();sample(fd);
    }
  else if(!strcmp(name,"integrity/transport_error"))
    {bus.next_transfer_error=-EIO;sample_observed(fd,true);bench_finish_transient(&bus.next_transfer_error);sample(fd);}
  else if(!strncmp(name,"integrity/partial_read_",23))
    {fault_offset=name[23]-'0';assert(fault_offset>=0 && fault_offset<8);sample_observed(fd,true);assert(fault_hit);sample(fd);sample(fd);}
  else if(!strncmp(name,"integrity/invalid_",18))
    {failure(fd);invalid=0;bench_wire_delay(period,1000000);sample(fd);}
  else if(!strcmp(name,"diagnostic/stalled"))
    {sample(fd);stall=true;failure(fd);stall=false;sample(fd);}
  else return 65;
done:
  assert(close(fd)==0);idle();return 0;
}
