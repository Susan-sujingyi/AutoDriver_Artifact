/* ST DocID018771 Rev2 section5.1.3/7.2 magnetic register lock model.
 * Six output registers remain locked after the first byte until all six
 * addresses have been read. Byte timing, mutexes and Sensor are shared/native.
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
#include <nuttx/sensors/lsm303dlhc.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "scenario.h"

struct witness {unsigned epoch,mask;int16_t raw[3];uint64_t acquired;};
static struct witness history[64];
static struct bench_i2c bus;
static uint8_t regs[13],pointer;
static int16_t frame[3];
static uint64_t started,delivered_stamp;
static unsigned epoch,epoch_base,read_mask,delivered,input,write_offset;
static bool ready,locked,changing;
static int partial_bytes=-1;
static unsigned partial_seen;

static void defaults(void)
{
  memset(regs,0,sizeof(regs));regs[0]=0x10;regs[1]=0x20;regs[2]=3;
  regs[10]=0x48;regs[11]=0x34;regs[12]=0x33;
}
static void tick(uint64_t now)
{
  if(started && !locked && now>=started+66667)
    {
      unsigned n=1+(now-started-66667)/66667;
      if(epoch_base+n>epoch)
        {
          epoch=epoch_base+n;ready=true;read_mask=0;
          for(unsigned a=0;a<3;a++)frame[a]=changing ? (int)((epoch*(unsigned[]){137,719,3109}[a]+a*911)%4096)-2048 : vectors[input].raw[a];
        }
    }
}
static void configuration(void){assert(regs[0]==0x10 && regs[1]==0x20 && regs[2]==0);}
static unsigned next_register(unsigned r){return r==8 ? 3 : r>=12 ? 0 : r+1;}
static void write_register(unsigned r,uint8_t value)
{
  if(r>2)bench_model_unsupported("lsm303dlhc_readonly_register_write");
  uint8_t before=regs[r];regs[r]=value&(uint8_t[]){0x9c,0xe0,3}[r];
  if(r==2)
    {
      if(regs[2]==0 && before!=0){started=bench_now_us();epoch_base=epoch;ready=false;read_mask=0;locked=false;}
      else if(regs[2]!=0){started=0;ready=false;locked=false;read_mask=0;}
    }
}
static uint8_t read_register(unsigned r)
{
  if(r==9)return (ready?1:0)|(locked?2:0);
  if(r>=3 && r<=8)
    {
      unsigned byte=r-3,axis=(unsigned[]){0,2,1}[byte/2];
      if(!epoch)return 0;
      locked=true;read_mask|=1u<<byte;
      struct witness *w=&history[epoch%64];
      if(w->epoch!=epoch){memset(w,0,sizeof(*w));w->epoch=epoch;memcpy(w->raw,frame,sizeof(frame));}
      w->mask|=1u<<byte;if(w->mask==63 && !w->acquired)w->acquired=bench_now_us();
      uint8_t value=(uint16_t)frame[axis]>>(byte%2?0:8);
      if(read_mask==63){locked=false;ready=false;read_mask=0;}
      return value;
    }
  if(r<13)return regs[r];
  bench_model_unsupported("lsm303dlhc_nonmagnetic_register");return 0;
}
static int message(void *context,struct i2c_msg_s *msg,bool start,bool repeated,bool stop)
{
  assert(msg->addr==BENCH_I2C_ADDRESS && !(msg->flags&I2C_M_TEN));tick(bench_now_us());
  if(msg->flags&I2C_M_READ)return 0;
  if(start)write_offset=0;
  for(unsigned i=0;i<msg->length;i++)
    {
      if(write_offset++==0)pointer=msg->buffer[i];
      else {write_register(pointer,msg->buffer[i]);pointer=next_register(pointer);}
    }
  return 0;
}
static int read_byte(void *context,const struct i2c_msg_s *msg,unsigned index,uint8_t *value)
{
  tick(bench_now_us());unsigned r=pointer;
  *value=read_register(r);pointer=next_register(pointer);
  if(r>=3 && r<=8 && partial_bytes>0 && ++partial_seen==(unsigned)partial_bytes)
    {partial_bytes=-1;history[epoch%64].mask=0;history[epoch%64].acquired=0;return -EIO;}
  return 0;
}
static const struct bench_i2c_device device={.message=message,.read_byte=read_byte};
static int setup(void)
{
  defaults();bench_i2c_init(&bus,&device,NULL,100000);
  uint64_t begin=bench_now_us();assert(lsm303dlhc_register(&bus.dev,BENCH_I2C_ADDRESS,0)==0);
  assert(bench_now_us()-begin<=250000);configuration();
  int fd=open("/dev/uorb/sensor_mag0",O_RDONLY);assert(fd>=0);return fd;
}
static bool sample_observed(int fd, bool permit_failure)
{
  struct sensor_mag event;memset(&event,0xa5,sizeof(event));uint64_t begin=bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd,&event,sizeof(event), sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - begin <= 500000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return 0;
    }

  assert(observed_result==sizeof(event) && bench_now_us()-begin<=500000);
  assert(isnan(event.temperature) && event.status==0 && event.timestamp<=bench_now_us() && event.timestamp>delivered_stamp);
  configuration();float xyz[]={event.x,event.y,event.z};unsigned match=0;bool current=false;
  for(unsigned i=0;i<64;i++)
    {
      struct witness *w=&history[i];
      if(w->epoch<=delivered || w->epoch<=match || w->mask!=63 || event.timestamp<w->acquired)continue;
      bool same=true,selected=true;
      for(unsigned a=0;a<3;a++)
        {
          double expected=w->raw[a]*scale[a],tolerance=0.001+2*FLT_EPSILON*fmax(1,fabs(expected));
          same&=isfinite(xyz[a]) && fabs(xyz[a]-expected)<=tolerance;
          selected&=w->raw[a]==vectors[input].raw[a];
        }
      if(same){match=w->epoch;current=selected;}
    }
  assert(match>delivered);delivered=match;delivered_stamp=event.timestamp;
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  printf("BENCHSIM {\"event\":\"sample\",\"epoch\":%u,\"x\":%.8f,\"y\":%.8f,\"z\":%.8f}\n",match,(double)event.x,(double)event.y,(double)event.z);
  return current;
}
static bool sample(int fd)
{
  return sample_observed(fd, false);
}
static void failure(int fd)
{
  struct sensor_mag event,before;memset(&event,0xa5,sizeof(event));memcpy(&before,&event,sizeof(event));
  uint64_t begin=bench_now_us();assert(read(fd,&event,sizeof(event))<0 && !memcmp(&event,&before,sizeof(event)));
  assert(bench_now_us()-begin<=500000 && !bus.active && !nxmutex_is_locked(&bus.mutex));
}
int bench_case_main(int argc,char **argv)
{
  if(argc!=2)return 64;const char *name=argv[1];
  if(!strcmp(name,"model/frame_lock"))
    {
      defaults();started=1;tick(66668);assert(epoch==1 && ready);
      read_register(3);assert(locked);tick(200002);assert(epoch==1);
      for(unsigned r=4;r<=8;r++)read_register(r);
      assert(!locked && !ready && history[1].mask==63);
      tick(200002);assert(epoch==3 && ready);return 0;
    }
  for(unsigned i=0;i<BENCH_VECTOR_COUNT;i++)if(!strcmp(name,vectors[i].id))
    {input=i;int fd=setup();assert(sample(fd));assert(close(fd)==0);return 0;}
  int fd=setup();
  if(!strcmp(name,"data/coherence")){changing=true;for(unsigned i=0;i<20;i++)sample(fd);}
  else if(!strcmp(name,"data/stable_change") || !strcmp(name,"os/lifecycle"))
    {
      sample(fd);sample(fd);input=BENCH_VECTOR_COUNT-1;bool fresh=false;
      uint64_t end=bench_now_us()+2000000;while(bench_now_us()<end && !fresh)fresh=sample(fd);assert(fresh);
      if(!strcmp(name,"os/lifecycle"))
        {
          char small[1];assert(read(fd,small,1)<0);assert(close(fd)==0);
          unsigned before=bus.messages;bench_sensor_check_disabled(SENSOR_TYPE_MAGNETIC_FIELD,0,sizeof(struct sensor_mag));
          assert(bus.messages==before);fd=open("/dev/uorb/sensor_mag0",O_RDONLY);assert(fd>=0);sample(fd);
        }
    }
  else if(!strcmp(name,"integrity/transport_error"))
    {bus.next_transfer_error=-EIO;sample_observed(fd,true);bench_finish_transient(&bus.next_transfer_error);sample(fd);}
  else if(!strcmp(name,"integrity/partial_read"))
    {for(unsigned i=1;i<=6;i++){partial_bytes=i;partial_seen=0;sample_observed(fd,true);assert(partial_bytes==-1);sample(fd);}}
  else return 65;
  assert(close(fd)==0);return 0;
}
