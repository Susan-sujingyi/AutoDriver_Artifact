/* SPDX-License-Identifier: Apache-2.0
 * Private native New Sensor reference. Bosch DS1.4 register protocol;
 * compensation algebra independently expressed from frozen SensorAPI float
 * equations. No source-OS driver or Legacy lower-half dependency.
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
struct bmm_device {
  struct sensor_lowerhalf_s lower;
  struct i2c_master_s *bus;
  uint8_t address;
  bool active;
  mutex_t lock;
  int x1,y1,x2,y2,xy1,xy2,z1,z2,z3,z4,xyz1;
};
static int transfer(struct bmm_device *dev,uint8_t reg,uint8_t *data,unsigned count,bool read,uint64_t end)
{
  uint8_t bytes[22]={reg};
  if(!read)memcpy(bytes+1,data,count);
  struct i2c_msg_s messages[2]={
    {.frequency=100000,.addr=dev->address,.flags=0,.buffer=bytes,.length=read?1:count+1},
    {.frequency=100000,.addr=dev->address,.flags=I2C_M_READ,.buffer=data,.length=count}};
  if(sensor_get_timestamp()>=end)return -ETIMEDOUT;
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
static int signed_value(unsigned value,unsigned bits)
{return value&(1u<<(bits-1))?(int)value-(int)(1u<<bits):(int)value;}
static unsigned word(const uint8_t *p){return p[0]|((unsigned)p[1]<<8);}
static int compensate(struct bmm_device *dev,const uint8_t *bytes,struct sensor_mag *sample)
{
  int x=signed_value(word(bytes)>>3,13);
  int y=signed_value(word(bytes+2)>>3,13);
  int z=signed_value(word(bytes+4)>>1,15);
  unsigned hall=word(bytes+6)>>2;
  if(x==-4096 || y==-4096 || z==-16384 || !hall || !dev->z1 || !dev->z2 || !dev->xyz1)return -EIO;
  double ratio=(double)dev->xyz1/hall-1.0;
  double polynomial=dev->xy2*ratio*ratio+dev->xy1*ratio+256.0;
  double denominator=dev->z2+(double)dev->z1*hall/32768.0;
  if(denominator==0.0)return -EIO;
  double values[3]={x*(dev->x2+160.0)/131072.0*polynomial+dev->x1/2.0,
                    y*(dev->y2+160.0)/131072.0*polynomial+dev->y1/2.0,
                    ((z-dev->z4)*131072.0-dev->z3*(double)((int)hall-dev->xyz1))/64.0/denominator};
  for(unsigned i=0;i<3;i++)if(!isfinite(values[i]) || values[i]*1000.0<INT32_MIN || values[i]*1000.0>=(double)INT32_MAX+1.0)return -ERANGE;
  sample->x=values[0];sample->y=values[1];sample->z=values[2];
  return 0;
}
static int activate(struct sensor_lowerhalf_s *lower,struct file *filep,bool enable)
{
  struct bmm_device *dev=(struct bmm_device *)lower;
  int ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  dev->active=enable;nxmutex_unlock(&dev->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct bmm_device *dev=(struct bmm_device *)lower;struct sensor_mag sample;
  uint8_t status,bytes[8];uint64_t end=sensor_get_timestamp()+250000;int ret;
  if(!buffer || length<sizeof(sample))return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  if(!dev->active){ret=-EACCES;goto done;}
  for(;;){
    if((ret=transfer(dev,0x48,&status,1,true,end))<0)goto done;
    if(status&1)break;
    uint64_t now=sensor_get_timestamp();
    if(now+30000>=end){ret=-ETIMEDOUT;goto done;}
    if((ret=wait_until(now+5000))<0)goto done;
  }
  if((ret=transfer(dev,0x42,bytes,8,true,end))<0)goto done;
  memset(&sample,0,sizeof(sample));
  if((ret=compensate(dev,bytes,&sample))<0)goto done;
  sample.temperature=NAN;sample.status=0;sample.timestamp=sensor_get_timestamp();
  memcpy(buffer,&sample,sizeof(sample));ret=sizeof(sample);
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int bmm150_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  static const uint8_t config[][2]={{0x51,4},{0x52,14},{0x4c,0}};
  uint64_t end=sensor_get_timestamp()+100000;struct bmm_device *dev;int ret;
  uint8_t value,trim[21];
  if(!i2c || address!=0x13 || devno<0)return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if(!dev)return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  if((ret=wait_until(sensor_get_timestamp()+1000))<0)goto fail;
  value=1;if((ret=transfer(dev,0x4b,&value,1,false,end))<0)goto fail;
  if((ret=wait_until(sensor_get_timestamp()+3000))<0)goto fail;
  if((ret=transfer(dev,0x40,&value,1,true,end))<0)goto fail;
  if(value!=0x32){ret=-ENODEV;goto fail;}
  if((ret=transfer(dev,0x5d,trim,21,true,end))<0)goto fail;
  dev->x1=signed_value(trim[0],8);dev->y1=signed_value(trim[1],8);
  dev->z4=signed_value(word(trim+5),16);dev->x2=signed_value(trim[7],8);dev->y2=signed_value(trim[8],8);
  dev->z2=signed_value(word(trim+11),16);dev->z1=word(trim+13);dev->xyz1=word(trim+15)&0x7fff;
  dev->z3=signed_value(word(trim+17),16);dev->xy2=signed_value(trim[19],8);dev->xy1=trim[20];
  if(!dev->z1 || !dev->z2 || !dev->xyz1){ret=-EIO;goto fail;}
  for(unsigned i=0;i<3;i++){
    value=config[i][1];if((ret=transfer(dev,config[i][0],&value,1,false,end))<0)goto fail;
  }
  dev->lower.type=SENSOR_TYPE_MAGNETIC_FIELD;dev->lower.ops=&ops;
  ret=sensor_register(&dev->lower,devno);if(ret<0)goto fail;
  return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
