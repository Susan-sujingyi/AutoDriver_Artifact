/* SPDX-License-Identifier: Apache-2.0
 * Private native New Sensor reference, MPU6050 PS Rev3.4 / RM Rev4.2.
 * Board-specific identity compatibility stays private, as in the old reference.
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
struct mpu_device {
  struct sensor_lowerhalf_s lower;
  struct i2c_master_s *bus;
  uint8_t address;
  bool active;
  mutex_t lock;
};
static int transfer(struct mpu_device *dev,uint8_t reg,uint8_t *data,unsigned count,bool read,uint64_t end)
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
static int wait_until(uint64_t end)
{
  for(;;){uint64_t now=sensor_get_timestamp();if(now>=end)return 0;
    int ret=nxsig_usleep(end-now);if(ret<0 && ret!=-EINTR)return ret;}
}
static int activate(struct sensor_lowerhalf_s *lower,struct file *filep,bool enable)
{
  struct mpu_device *dev=(struct mpu_device *)lower;
  int ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  dev->active=enable;nxmutex_unlock(&dev->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct mpu_device *dev=(struct mpu_device *)lower;
  struct sensor_accel sample;
  uint8_t status,bytes[14];
  uint64_t end=sensor_get_timestamp()+100000;
  int ret;
  if(!buffer || length<sizeof(sample))return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  if(!dev->active){ret=-EACCES;goto done;}
  for(;;){
    if((ret=transfer(dev,0x3a,&status,1,true,end))<0)goto done;
    if(status&1)break;
    uint64_t now=sensor_get_timestamp();
    if(now+30000>=end){ret=-ETIMEDOUT;goto done;}
    if((ret=wait_until(now+1000))<0)goto done;
  }
  if((ret=transfer(dev,0x3b,bytes,6,true,end))<0)goto done;
  memset(&sample,0,sizeof(sample));
  float xyz[3];
  for(unsigned i=0;i<3;i++){
    int16_t raw=((uint16_t)bytes[2*i]<<8)|bytes[2*i+1];
    xyz[i]=(double)raw*9.80665/16384.0;
  }
  sample.x=xyz[0];sample.y=xyz[1];sample.z=xyz[2];
  sample.temperature=NAN;sample.timestamp=sensor_get_timestamp();
  /* A newly raised ready flag during the burst must not authorize rereading
   * a just-delivered conversion. Flushing may skip an epoch, which is allowed. */
  if((ret=transfer(dev,0x3a,&status,1,true,end))<0)goto done;
  memcpy(buffer,&sample,sizeof(sample));ret=sizeof(sample);
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int mpu6050_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  static const uint8_t config[][2]={{0x6b,0},{0x6c,0},{0x1c,0},{0x1b,0},
    {0x19,9},{0x1a,0},{0x23,0},{0x6a,0},{0x38,1}};
  uint64_t end=sensor_get_timestamp()+100000;
  struct mpu_device *dev;uint8_t value;int ret;
  if(!i2c || address!=0x68 || devno<0)return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if(!dev)return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  if((ret=transfer(dev,0x75,&value,1,true,end))<0)goto fail;
  if(value!=0x68 && value!=0x70){ret=-ENODEV;goto fail;}
  for(unsigned i=0;i<sizeof(config)/sizeof(config[0]);i++){
    value=config[i][1];if((ret=transfer(dev,config[i][0],&value,1,false,end))<0)goto fail;
  }
  dev->lower.type=SENSOR_TYPE_ACCELEROMETER;dev->lower.ops=&ops;
  ret=sensor_register(&dev->lower,devno);if(ret<0)goto fail;
  return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
