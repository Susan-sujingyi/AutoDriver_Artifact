/* SPDX-License-Identifier: Apache-2.0
 * Private native New Sensor reference. ST DocID018771 Rev2, magnetic block.
 * Nominal XY/Z sensitivities from Table75; no Legacy driver dependency.
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
struct lsm_device {
  struct sensor_lowerhalf_s lower;
  struct i2c_master_s *bus;
  uint8_t address;
  bool active;
  mutex_t lock;
};
static int transfer(struct lsm_device *dev,uint8_t reg,uint8_t *data,unsigned count,bool read,uint64_t end)
{
  uint8_t bytes[4]={reg};
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
static int activate(struct sensor_lowerhalf_s *lower,struct file *filep,bool enable)
{
  struct lsm_device *dev=(struct lsm_device *)lower;
  int ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  dev->active=enable;nxmutex_unlock(&dev->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct lsm_device *dev=(struct lsm_device *)lower;struct sensor_mag sample;
  uint8_t status,bytes[6];uint64_t end=sensor_get_timestamp()+500000;int ret;
  if(!buffer || length<sizeof(sample))return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  if(!dev->active){ret=-EACCES;goto done;}
  for(;;){
    if((ret=transfer(dev,9,&status,1,true,end))<0)goto done;
    if(status&1)break;
    uint64_t now=sensor_get_timestamp();
    if(now+30000>=end){ret=-ETIMEDOUT;goto done;}
    if((ret=wait_until(now+5000))<0)goto done;
  }
  if((ret=transfer(dev,3,bytes,6,true,end))<0)goto done;
  memset(&sample,0,sizeof(sample));
  int16_t x=((uint16_t)bytes[0]<<8)|bytes[1];
  int16_t z=((uint16_t)bytes[2]<<8)|bytes[3];
  int16_t y=((uint16_t)bytes[4]<<8)|bytes[5];
  sample.x=x*100.0/1100;sample.y=y*100.0/1100;sample.z=z*100.0/980;
  sample.temperature=NAN;sample.status=0;sample.timestamp=sensor_get_timestamp();
  memcpy(buffer,&sample,sizeof(sample));ret=sizeof(sample);
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int lsm303dlhc_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  static const uint8_t config[][2]={{0,0x10},{1,0x20},{2,0}};
  uint64_t end=sensor_get_timestamp()+250000;struct lsm_device *dev;int ret;
  if(!i2c || address!=0x1e || devno<0)return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if(!dev)return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  for(unsigned i=0;i<3;i++){
    uint8_t value=config[i][1];if((ret=transfer(dev,config[i][0],&value,1,false,end))<0)goto fail;
  }
  dev->lower.type=SENSOR_TYPE_MAGNETIC_FIELD;dev->lower.ops=&ops;
  ret=sensor_register(&dev->lower,devno);if(ret<0)goto fail;
  return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
