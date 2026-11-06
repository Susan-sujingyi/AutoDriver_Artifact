/* SPDX-License-Identifier: Apache-2.0
 * Private native New Sensor reference. Infineon DPS310 datasheet V1.2
 * coefficient polynomial and documented manufacturer temperature correction.
 * No Legacy wrapper; no source-side ignored-error or stale-cache behavior.
 */
#include <nuttx/config.h>
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <nuttx/kmalloc.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/i2c/i2c_master.h>
struct dps_device;
struct dps_node {
  struct sensor_lowerhalf_s lower;
  struct dps_device *owner;
  unsigned channel;
  bool active;
};
struct dps_device {
  struct i2c_master_s *bus;
  uint8_t address;
  mutex_t lock;
  int32_t coefficients[9];
  struct dps_node nodes[2];
};
static int transfer(struct dps_device *dev,uint8_t reg,uint8_t *data,unsigned count,bool read,uint64_t end)
{
  uint8_t bytes[5]={reg};
  if(!read && count)memcpy(bytes+1,data,count);
  struct i2c_msg_s messages[2]={
    {.frequency=100000,.addr=dev->address,.flags=0,.buffer=bytes,.length=read?1:count+1},
    {.frequency=100000,.addr=dev->address,.flags=I2C_M_READ,.buffer=data,.length=count}};
  /* Register reads have two address phases plus the register byte;
   * writes have one address phase plus the register byte. Reserve their
   * nominal wire time before starting, including ACK/NACK clocks. This
   * does not bound arbitrary clock stretching or OS scheduling latency. */
  uint64_t now=sensor_get_timestamp();
  uint64_t wire_us=((uint64_t)(count+(read?3:2))*9*1000000+
                    messages[0].frequency-1)/messages[0].frequency;
  if(now>=end || wire_us>end-now)return -ETIMEDOUT;
  int ret=I2C_TRANSFER(dev->bus,messages,read?2:1);
  if(sensor_get_timestamp()>end)return -ETIMEDOUT;
  return ret<0?ret:0;
}
static int wait_until(uint64_t end)
{
  while(sensor_get_timestamp()<end){
    int ret=nxsig_usleep(end-sensor_get_timestamp());
    if(ret<0 && ret!=-EINTR)return ret;
  }
  return 0;
}
static int poll(struct dps_device *dev,uint8_t mask,uint8_t expected,uint64_t end)
{
  for(;;){
    uint8_t status;int ret=transfer(dev,8,&status,1,true,end);
    if(ret<0)return ret;
    if((status&mask)==expected)return 0;
  }
}
static int32_t sign_extend(uint32_t value,unsigned bits)
{return value&(1u<<(bits-1))?(int32_t)value-(int32_t)(1u<<bits):(int32_t)value;}
static int32_t raw24(const uint8_t *b)
{return sign_extend(((uint32_t)b[0]<<16)|((uint32_t)b[1]<<8)|b[2],24);}
static void calibration(struct dps_device *dev,const uint8_t *b)
{
  int32_t *c=dev->coefficients;
  c[0]=sign_extend(((unsigned)b[0]<<4)|(b[1]>>4),12);
  c[1]=sign_extend(((unsigned)(b[1]&15)<<8)|b[2],12);
  c[2]=sign_extend(((uint32_t)b[3]<<12)|((uint32_t)b[4]<<4)|(b[5]>>4),20);
  c[3]=sign_extend(((uint32_t)(b[5]&15)<<16)|((uint32_t)b[6]<<8)|b[7],20);
  for(unsigned i=0;i<5;i++)c[4+i]=sign_extend(((unsigned)b[8+2*i]<<8)|b[9+2*i],16);
}
static int measure(struct dps_device *dev,unsigned channel,int32_t *raw,uint64_t end)
{
  int ret=poll(dev,7,0,end);if(ret<0)return ret;
  uint8_t value=channel?1:2,bytes[3];
  if((ret=transfer(dev,8,&value,1,false,end))<0)return ret;
  if((ret=poll(dev,channel?0x10:0x20,channel?0x10:0x20,end))<0)return ret;
  if((ret=transfer(dev,channel?0:3,bytes,3,true,end))<0)return ret;
  *raw=raw24(bytes);return 0;
}
static int activate(struct sensor_lowerhalf_s *lower,struct file *filep,bool enabled)
{
  struct dps_node *node=(struct dps_node *)lower;
  int ret=nxmutex_trylock(&node->owner->lock);if(ret<0)return ret;
  node->active=enabled;nxmutex_unlock(&node->owner->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct dps_node *node=(struct dps_node *)lower;struct dps_device *dev=node->owner;
  union{struct sensor_temp t;struct sensor_baro p;} sample;
  size_t size=node->channel?sizeof(sample.p):sizeof(sample.t);
  uint64_t end=sensor_get_timestamp()+100000;int32_t raw_t,raw_p;int ret;
  if(!buffer || length<size)return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  if(!node->active){ret=-EACCES;goto done;}
  if((ret=measure(dev,0,&raw_t,end))<0)goto done;
  double t=raw_t/7864320.0;const int32_t *c=dev->coefficients;
  memset(&sample,0,sizeof(sample));
  if(node->channel){
    if((ret=measure(dev,1,&raw_p,end))<0)goto done;
    double p=raw_p/7864320.0;
    sample.p.pressure=(c[2]+p*(c[3]+p*(c[6]+p*c[8]))+t*c[4]+t*p*(c[5]+p*c[7]))/100.0;
    sample.p.temperature=NAN;sample.p.timestamp=sensor_get_timestamp();
  }else{sample.t.temperature=c[0]/2.0+c[1]*t;sample.t.timestamp=sensor_get_timestamp();}
  memcpy(buffer,&sample,size);ret=size;
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int dps310_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  const int types[2]={SENSOR_TYPE_AMBIENT_TEMPERATURE,SENSOR_TYPE_BAROMETER};
  uint64_t end=sensor_get_timestamp()+100000;
  struct dps_device *dev;uint8_t value,trim[18];int ret;
  if(!i2c || address!=0x77 || devno<0)return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if(!dev)return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  if((ret=wait_until(sensor_get_timestamp()+2500))<0)goto fail;
  if((ret=transfer(dev,0x0d,&value,1,true,end))<0)goto fail;
  if(value!=0x10){ret=-ENODEV;goto fail;}
  if((ret=poll(dev,0xc0,0xc0,end))<0)goto fail;
  if((ret=transfer(dev,0x10,trim,18,true,end))<0)goto fail;
  calibration(dev,trim);
  if((ret=transfer(dev,0x28,&value,1,true,end))<0)goto fail;
  uint8_t config[4]={3,(value&0x80)|3,0,0};
  if((ret=transfer(dev,6,config,4,false,end))<0)goto fail;
  const uint8_t correction[][2]={{0x0e,0xa5},{0x0f,0x96},{0x62,2},{0x0e,0},{0x0f,0}};
  for(unsigned i=0;i<5;i++){
    value=correction[i][1];if((ret=transfer(dev,correction[i][0],&value,1,false,end))<0)goto fail;
  }
  for(unsigned i=0;i<2;i++){
    dev->nodes[i].owner=dev;dev->nodes[i].channel=i;
    dev->nodes[i].lower.type=types[i];dev->nodes[i].lower.ops=&ops;
    ret=sensor_register(&dev->nodes[i].lower,devno);
    if(ret<0){while(i)sensor_unregister(&dev->nodes[--i].lower,devno);goto fail;}
  }
  return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
