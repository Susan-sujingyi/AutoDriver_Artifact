/* LSM6DSO DS12140 Rev3 / AN5192 Rev5. Pair-level BDU is deliberately not
 * whole-XYZ BDU. Read bytes advance through the common native I2C peripheral.
 * The frozen NuttX public ABI additionally requires coherent XYZ epochs.
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
#include <nuttx/sensors/lsm6dso.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "scenario.h"

struct pair {unsigned epoch, mask; int16_t value; bool settled;};
struct witness {unsigned epoch, mask; int16_t raw[3]; bool settled; uint64_t at;};
static struct pair pairs[3];
static struct witness history[128];
static struct bench_i2c bus;
static uint8_t regs[128], pointer, bank, initial_bank;
static int16_t frame[3];
static uint64_t started, reset_end;
static unsigned epoch, epoch_base, ordinal, input, delivered, write_offset;
static bool ready, changing;
static int partial_bytes = -1;
static unsigned partial_seen;

static void defaults(void)
{
  memset(regs,0,sizeof(regs));regs[2]=0x3f;regs[0x12]=4;regs[0x18]=0xe0;
  bank=0;started=0;ordinal=0;ready=false;memset(pairs,0,sizeof(pairs));
}
static void tick(uint64_t now)
{
  if(reset_end){if(now<reset_end)return;reset_end=0;defaults();}
  if(started && now>=started+9616)
    {
      unsigned n=1+(now-started-9616)*104/1000000;
      if(epoch_base+n>epoch)
        {
          ordinal=n;epoch=epoch_base+n;
          for(unsigned i=0;i<3;i++)frame[i]=changing ? (int16_t)(epoch*(unsigned[]){137,7919,3109}[i]+i*10000) : vectors[input].raw[i];
          if(n>1 || !(regs[0x13]&8))ready=true;
        }
    }
}
static void configuration(void)
{
  assert(bank==0 && !reset_end);
  assert((regs[0x10]&0xf3)==0x40);
  unsigned fs=(regs[0x10]>>2)&3;assert(fs==0 || (fs==1 && (regs[0x17]&2)));
  assert((regs[0x11]&0xf0)==0 && !(regs[0x13]&4));
  assert(regs[0x14]==0 && (regs[0x15]&0xf0)==0 && (regs[0x16]&2)==0);
  assert((regs[0x17]&0xfc)==0 && (regs[0x0a]&7)==0);
  /* BDU and IF_INC are implementation choices, checked through data effects. */
}
static bool writable(unsigned r)
{return r==2 || (r>=7 && r<=0x0e) || (r>=0x10 && r<=0x19) || r==0x62 || (r>=0x73 && r<=0x75);}
static void write_register(unsigned r,uint8_t value)
{
  assert(r<128);
  if(r==1){if(value!=0 && value!=0x40 && value!=0x80)bench_model_unsupported("lsm6dso_bank");bank=value;return;}
  if(bank){if(bank==0x40 && r==0x14 && !(value&4))return;bench_model_unsupported("lsm6dso_nonuser_bank_write");}
  if(reset_end)bench_model_unsupported("lsm6dso_reset_overlap");
  if(!writable(r))bench_model_unsupported("lsm6dso_register_write");
  if(r==0x12)
    {
      if(value&0x80)bench_model_unsupported("lsm6dso_boot");
      assert(!(value&2));
      if(value&1){assert(!(regs[0x10]&0xf0) && !(regs[0x11]&0xf0));reset_end=bench_now_us()+50;regs[r]=value;started=0;ready=false;return;}
    }
  if(r==0x10)
    {
      if(!(value&0xf0)){started=0;ready=false;}
      else if(!(regs[r]&0xf0)){started=bench_now_us();epoch_base=epoch;ordinal=0;ready=false;memset(pairs,0,sizeof(pairs));}
    }
  regs[r]=value;
}
static uint8_t read_register(unsigned r)
{
  assert(r<128);
  if(r==1)return bank;
  if(bank)bench_model_unsupported("lsm6dso_nonuser_bank_read");
  if(r==0x0f)return 0x6c;
  if(r==0x1e)return ready ? 1 : 0;
  if(r>=0x20 && r<=0x27)return 0xa5;
  if(r>=0x28 && r<=0x2d)
    {
      unsigned byte=r-0x28,axis=byte/2,part=byte%2;
      struct pair p=pairs[axis];
      if(!(regs[0x12]&0x40) || !p.mask)p=(struct pair){.epoch=epoch,.value=frame[axis],.settled=ordinal>1};
      p.mask|=1u<<part;
      struct witness *w=&history[p.epoch%128];
      if(w->epoch!=p.epoch){memset(w,0,sizeof(*w));w->epoch=p.epoch;w->settled=true;}
      w->mask|=1u<<byte;w->raw[axis]=p.value;w->settled&=p.settled;w->at=bench_now_us();
      if(regs[0x12]&0x40){pairs[axis]=p;if(p.mask==3)pairs[axis].mask=0;}
      if(part)ready=false;
      return (uint16_t)p.value>>(part*8);
    }
  if(!writable(r))bench_model_unsupported("lsm6dso_register_read");
  return regs[r];
}
static int message(void *context,struct i2c_msg_s *msg,bool start,bool repeated,bool stop)
{
  assert(msg->addr==BENCH_I2C_ADDRESS && !(msg->flags&I2C_M_TEN));
  tick(bench_now_us());
  if(msg->flags&I2C_M_READ)return 0; /* Per-byte hook supplies actual data. */
  if(start)write_offset=0;
  for(unsigned i=0;i<msg->length;i++)
    {
      if(write_offset++==0)pointer=msg->buffer[i];
      else {unsigned step=(regs[0x12]&4)?1:0;write_register(pointer,msg->buffer[i]);pointer+=step;}
    }
  return 0;
}
static int read_byte(void *context,const struct i2c_msg_s *msg,unsigned index,uint8_t *value)
{
  tick(bench_now_us());unsigned r=pointer;
  /* A failed byte is not delivered by the bus. Preserve physical read side
   * effects, but do not record that byte as candidate acquisition evidence. */
  struct witness before[128];memcpy(before,history,sizeof(before));
  *value=read_register(r);pointer+=(regs[0x12]&4)?1:0;
  if(bank==0 && r>=0x28 && r<=0x2d && partial_bytes>0 && ++partial_seen==(unsigned)partial_bytes)
    {partial_bytes=-1;memcpy(history,before,sizeof(history));return -EIO;}
  return 0;
}
static const struct bench_i2c_device device={.message=message,.read_byte=read_byte};

static int setup(void)
{
  defaults();bank=initial_bank;bench_i2c_init(&bus,&device,NULL,100000);
  uint64_t begin=bench_now_us();assert(lsm6dso_register(&bus.dev,BENCH_I2C_ADDRESS,0)==0);
  assert(bench_now_us()-begin<=100000);configuration();
  int fd=open("/dev/uorb/sensor_accel0",O_RDONLY);assert(fd>=0);return fd;
}
static bool sample_observed(int fd, bool permit_failure)
{
  struct sensor_accel event;memset(&event,0xa5,sizeof(event));
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
  assert(bench_now_us()-begin<=100000 && event.timestamp<=bench_now_us() && isnan(event.temperature));
  configuration();float xyz[]={event.x,event.y,event.z};unsigned match=0;
  bool current=false;
  for(unsigned i=0;i<128;i++)
    {
      struct witness *w=&history[i];
      if(w->epoch<=delivered || w->epoch<=match || w->mask!=63 || !w->settled || event.timestamp<w->at)continue;
      bool same=true,selected=true;
      for(unsigned axis=0;axis<3;axis++)
        {
          double expected=w->raw[axis]*BENCH_ACCEL_SCALE;
          double tolerance=0.000001+2*FLT_EPSILON*fmax(1,fabs(expected));
          same&=isfinite(xyz[axis]) && fabs(xyz[axis]-expected)<=tolerance;
          selected&=w->raw[axis]==vectors[input].raw[axis];
        }
      if(same){match=w->epoch;current=selected;}
    }
  assert(match>delivered);delivered=match;
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
  struct sensor_accel event,before;memset(&event,0xa5,sizeof(event));memcpy(&before,&event,sizeof(event));
  uint64_t begin=bench_now_us();assert(read(fd,&event,sizeof(event))<0 && !memcmp(&event,&before,sizeof(event)));
  assert(bench_now_us()-begin<=100000 && !bus.active && !nxmutex_is_locked(&bus.mutex));
}

int bench_case_main(int argc,char **argv)
{
  if(argc!=2)return 64;const char *name=argv[1];
  if(!strcmp(name,"model/pair_bdu"))
    {
      defaults();regs[0x12]|=0x40;epoch=1;ordinal=2;frame[0]=0x1234;
      assert(read_register(0x28)==0x34);epoch=2;ordinal=3;frame[0]=0x5678;
      assert(read_register(0x29)==0x12 && history[1].mask==3 && pairs[0].mask==0);
      assert(read_register(0x28)==0x78 && read_register(0x29)==0x56);
      assert(history[2].mask==3);return 0;
    }
  if(!strncmp(name,"initialization/bank_",20))initial_bank=atoi(name+20);
  for(unsigned i=0;i<BENCH_VECTOR_COUNT;i++)if(!strcmp(name,vectors[i].id))
    {input=i;int fd=setup();assert(sample(fd));assert(close(fd)==0);return 0;}
  int fd=setup();
  if(!strcmp(name,"data/coherence")){changing=true;for(unsigned i=0;i<20;i++)sample(fd);}
  else if(!strcmp(name,"data/stable_change") || !strcmp(name,"os/lifecycle"))
    {
      sample(fd);sample(fd);input=BENCH_VECTOR_COUNT-1;bool fresh=false;
      uint64_t end=bench_now_us()+1000000;while(bench_now_us()<end && !fresh)fresh=sample(fd);assert(fresh);
      if(!strcmp(name,"os/lifecycle"))
        {
          char small[1];assert(read(fd,small,1)<0);assert(close(fd)==0);
          unsigned before=bus.messages;bench_sensor_check_disabled(SENSOR_TYPE_ACCELEROMETER,0,sizeof(struct sensor_accel));
          assert(bus.messages==before);fd=open("/dev/uorb/sensor_accel0",O_RDONLY);assert(fd>=0);sample(fd);
        }
    }
  else if(!strncmp(name,"initialization/bank_",20))sample(fd);
  else if(!strcmp(name,"integrity/transport_error"))
    {bus.next_transfer_error=-EIO;sample_observed(fd,true);bench_finish_transient(&bus.next_transfer_error);sample(fd);}
  else if(!strcmp(name,"integrity/partial_read"))
    {for(unsigned i=1;i<=6;i++){partial_bytes=i;partial_seen=0;sample_observed(fd,true);assert(partial_bytes==-1);sample(fd);}}
  else return 65;
  assert(close(fd)==0);return 0;
}
