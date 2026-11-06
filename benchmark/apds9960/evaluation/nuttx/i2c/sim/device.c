/* AV02-4191EN digital proximity model. Real NuttX custom Sensor registration,
 * activation, fetch and synchronization; no count-to-distance conversion.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/apds9960.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "scenario.h"

static struct bench_i2c bus;
static uint8_t regs[256],pointer,input,value,extra_status;
static uint64_t power_ready,next_conversion,exit_end;
static unsigned generation,delivered,write_offset,outside_cycles;
static bool valid,pint;
struct acquisition {unsigned epoch;uint8_t raw;uint64_t at;};
static struct acquisition history[64];
static const char *path="/dev/uorb/proximity_raw0";

static void configuration(void)
{
  assert(regs[0x8e]==0x47 && (regs[0x8f]&0xcc)==4);
  assert((regs[0x90]&0xbf)==0x11 && (regs[0x9f]&0xef)==0);
  assert((regs[0x9d]&0x7f)==0 && (regs[0x9e]&0x7f)==0);
  assert((regs[0x8d]&0xfd)==0x60 && (regs[0x8f]&0x30)==0);
}
static void tick(uint64_t now)
{
  if(next_conversion && now>=next_conversion)
    {
      unsigned n=(now-next_conversion)/1200+1;next_conversion+=n*1200;
      generation+=n;value=input;valid=true;
      if(value<regs[0x89] || value>regs[0x8b])outside_cycles+=n;else outside_cycles=0;
      unsigned persistence=regs[0x8c]>>4;
      if(!persistence || outside_cycles>=persistence)pint=true;
    }
}
static void defaults(void)
{
  memset(regs,0,sizeof(regs));regs[0x81]=regs[0x83]=255;
  regs[0x8d]=0x60;regs[0x8e]=0x40;regs[0x90]=1;regs[0x92]=0xab;
}
static uint8_t read_register(unsigned r)
{
  if(r==0x92)return 0xab;
  if(r==0x93)return (valid?2:0)|(pint?0x20:0)|extra_status;
  if(r==0x9c)
    {
      configuration();assert(regs[0x80]==5);
      if(valid)history[generation%64]=(struct acquisition){generation,value,bench_now_us()};
      valid=false;return value;
    }
  if(r<256)return regs[r];
  bench_model_unsupported("apds9960_register_read");return 0;
}
static void write_register(unsigned r,uint8_t byte)
{
  if(r>=256 || (r>=0x92 && r<=0x9c) || (r>=0xae && r<=0xaf) || r>=0xfc)
    bench_model_unsupported("apds9960_register_write");
  uint8_t mask=r==0x8e?255:r==0x8f?0xcc:r==0x90?0x30:(r==0x9d || r==0x9e)?0x7f:r==0x9f?0x2f:0;
  uint8_t old=regs[r];assert(!(regs[0x80]&4) || !((old^byte)&mask));
  if(r==0x80)assert((byte&~5)==0);
  if(r==0x8d)assert((byte&0xfd)==0x60);
  if(r==0x8f)assert((byte&0x30)==0);
  if(r==0x90)assert((byte&15)==1 && !(byte&0x80));
  if(r==0x9f)assert((byte&0xc0)==0);
  regs[r]=byte;
  if(r==0x80)
    {
      if((byte&1) && !(old&1))exit_end=bench_now_us()+7000;
      if((byte&5)==5 && (old&5)!=5)
        {configuration();uint64_t now=bench_now_us();next_conversion=(now>exit_end?now:exit_end)+1200;valid=false;}
      if((byte&5)!=5){next_conversion=0;valid=false;}
    }
}
static int address(void *context,uint16_t addr,bool reading)
{assert(addr==BENCH_I2C_ADDRESS);return bench_now_us()<power_ready ? -ENXIO:0;}
static int message(void *context,struct i2c_msg_s *msg,bool start,bool repeated,bool stop)
{
  assert(msg->addr==BENCH_I2C_ADDRESS && !(msg->flags&I2C_M_TEN));tick(bench_now_us());
  if(msg->flags&I2C_M_READ)
    {for(unsigned i=0;i<msg->length;i++)msg->buffer[i]=read_register(pointer++);return 0;}
  if(start)write_offset=0;
  for(unsigned i=0;i<msg->length;i++)
    {
      if(write_offset++==0)
        {
          pointer=msg->buffer[i];
          if(pointer>=0xe5 && pointer<=0xe7)
            {if(pointer!=0xe6){pint=false;outside_cycles=0;}if(msg->length>1)assert(msg->length==2 && msg->buffer[1]==0);return 0;}
        }
      else write_register(pointer++,msg->buffer[i]);
    }
  return 0;
}
static const struct bench_i2c_device device={.message=message,.address=address};
static int setup(void)
{
  defaults();bench_i2c_init(&bus,&device,NULL,100000);power_ready=bench_now_us()+5700;
  uint64_t begin=bench_now_us();assert(apds9960_register(&bus.dev,BENCH_I2C_ADDRESS,0)==0);
  assert(bench_now_us()-begin<=100000 && regs[0x80]==5);configuration();
  assert(bench_sensor_find_custom(path)!=NULL);
  int fd=open(path,O_RDONLY);assert(fd>=0);return fd;
}
static unsigned sample_observed(int fd, bool permit_failure)
{
  struct custom_proximity_raw event;memset(&event,0xa5,sizeof(event));
  uint64_t begin=bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd,&event,sizeof(event), sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - begin <= 100000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return 0;
    }
assert(observed_result==sizeof(event));
  assert(bench_now_us()-begin<=100000 && event.timestamp<=bench_now_us());
  unsigned match=0;
  for(unsigned i=0;i<64;i++)
    {struct acquisition *a=&history[i];if(a->epoch>delivered && a->epoch>match && a->at<=event.timestamp && event.raw==expected_raw[a->raw])match=a->epoch;}
  assert(match>delivered);delivered=match;
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));return event.raw;
}
static unsigned sample(int fd)
{
  return sample_observed(fd, false);
}
static void failure(int fd)
{
  struct custom_proximity_raw event,before;memset(&event,0xa5,sizeof(event));memcpy(&before,&event,sizeof(event));
  uint64_t begin=bench_now_us();assert(read(fd,&event,sizeof(event))<0 && !memcmp(&event,&before,sizeof(event)));
  assert(bench_now_us()-begin<=100000 && !bus.active && !nxmutex_is_locked(&bus.mutex));
}
int bench_case_main(int argc,char **argv)
{
  if(argc!=2)return 64;const char *name=argv[1];
  if(!strcmp(name,"model/proximity_state"))
    {
      defaults();regs[0x80]=5;regs[0x8e]=0x47;regs[0x8f]=4;regs[0x90]=0x11;
      next_conversion=100;input=0;tick(100);assert(generation==1 && valid);
      assert(read_register(0x9c)==0 && !valid && history[1].epoch==1);
      input=255;tick(1300);assert(generation==2 && read_register(0x9c)==255);return 0;
    }
  for(unsigned i=0;i<BENCH_VECTOR_COUNT;i++)if(!strcmp(name,vectors[i].id))
    {input=vectors[i].raw;int fd=setup();assert(sample(fd)==input);assert(close(fd)==0);return 0;}
  int fd=setup();
  if(!strcmp(name,"numeric/all_counts") || !strcmp(name,"data/stable_change"))
    {
      for(unsigned n=0;n<256;n++)
        {input=n;bool seen=false;uint64_t end=bench_now_us()+100000;while(!seen && bench_now_us()<end)seen=sample(fd)==n;assert(seen);}
    }
  else if(!strcmp(name,"protocol/unused_status_bits"))
    {extra_status=0xd5;input=237;assert(sample(fd)==input);}
  else if(!strcmp(name,"os/lifecycle"))
    {
      sample(fd);char small[1];assert(read(fd,small,1)<0);assert(close(fd)==0);
      unsigned before=bus.messages;bench_sensor_check_lower_disabled(bench_sensor_find_custom(path),sizeof(struct custom_proximity_raw));
      assert(bus.messages==before);fd=open(path,O_RDONLY);assert(fd>=0);sample(fd);
    }
  else if(!strcmp(name,"integrity/transport_error"))
    {bus.next_transfer_error=-EIO;sample_observed(fd,true);bench_finish_transient(&bus.next_transfer_error);sample(fd);}
  else return 65;
  assert(close(fd)==0);return 0;
}
