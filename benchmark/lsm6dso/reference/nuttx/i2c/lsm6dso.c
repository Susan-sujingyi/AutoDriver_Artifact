/* SPDX-License-Identifier: Apache-2.0
 * Private native New Sensor reference, LSM6DSO DS12140 Rev3 / AN5192 Rev5.
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
  uint8_t bytes[2]={reg,read ? 0 : *data};
  struct i2c_msg_s messages[2]={
    {.frequency=100000,.addr=dev->address,.flags=0,.buffer=bytes,.length=read ? 1 : 2},
    {.frequency=100000,.addr=dev->address,.flags=I2C_M_READ,.buffer=data,.length=count}};
  if(sensor_get_timestamp()>=end)return -ETIMEDOUT;
  int ret=I2C_TRANSFER(dev->bus,messages,read ? 2 : 1);
  if(sensor_get_timestamp()>end)return -ETIMEDOUT;
  return ret<0 ? ret : 0;
}
static int activate(struct sensor_lowerhalf_s *lower,struct file *filep,bool enable)
{
  struct lsm_device *dev=(struct lsm_device *)lower;
  int ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  dev->active=enable;nxmutex_unlock(&dev->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct lsm_device *dev=(struct lsm_device *)lower;
  struct sensor_accel sample;
  uint8_t status,bytes[14];
  uint64_t end=sensor_get_timestamp()+100000;
  int ret;
  if(!buffer || length<sizeof(sample))return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  if(!dev->active){ret=-EACCES;goto done;}
  /* Release any partial per-axis pair left by a prior failed read, then wait
   * for a new conversion. The status flag clears on acceleration high bytes. */
  if((ret=transfer(dev,0x28,bytes,6,true,end))<0)goto done;
  for(;;){
    if((ret=transfer(dev,0x1e,&status,1,true,end))<0)goto done;
    if(status&1)break;
    /* Poll transfers advance time; no scheduler-dependent short sleep. */
  }
  if((ret=transfer(dev,0x28,bytes,6,true,end))<0)goto done;
  memset(&sample,0,sizeof(sample));
  float xyz[3];
  for(unsigned i=0;i<3;i++){
    int16_t raw=((uint16_t)bytes[2*i+1]<<8)|bytes[2*i];
    xyz[i]=(double)raw*61.0*9.80665/1000000.0;
  }
  sample.x=xyz[0];sample.y=xyz[1];sample.z=xyz[2];
  sample.temperature=NAN;sample.timestamp=sensor_get_timestamp();
  memcpy(buffer,&sample,sizeof(sample));ret=sizeof(sample);
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int lsm6dso_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  static const uint8_t config[][2]={{0x12,0x44},{0x13,8},{0x18,0xe2},
    {0x11,0},{0x14,0},{0x15,0},{0x16,0},{0x17,0},{0x0a,0},{0x10,0x40}};
  uint64_t end=sensor_get_timestamp()+100000;
  struct lsm_device *dev;uint8_t value;int ret;
  if(!i2c || address!=0x6a || devno<0)return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if(!dev)return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  value=0;if((ret=transfer(dev,0x01,&value,1,false,end))<0)goto fail;
  if((ret=transfer(dev,0x0f,&value,1,true,end))<0)goto fail;
  if(value!=0x6c){ret=-ENODEV;goto fail;}
  for(unsigned i=0;i<sizeof(config)/sizeof(config[0]);i++){
    value=config[i][1];if((ret=transfer(dev,config[i][0],&value,1,false,end))<0)goto fail;
  }
  dev->lower.type=SENSOR_TYPE_ACCELEROMETER;dev->lower.ops=&ops;
  ret=sensor_register(&dev->lower,devno);if(ret<0)goto fail;
  return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
