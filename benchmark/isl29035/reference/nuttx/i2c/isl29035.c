/* SPDX-License-Identifier: Apache-2.0
 * Private reference: ISL29035 FN8371 Rev 3.00, sections Register Set and
 * Calculating Lux. Native New Sensor; no Legacy implementation dependency.
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
struct isl_device {
  struct sensor_lowerhalf_s lower;
  struct i2c_master_s *bus;
  uint8_t address;
  mutex_t lock;
  bool active;
};
static int transfer(struct isl_device *dev, uint8_t reg, uint8_t *data,
                    int size, bool read, uint64_t deadline)
{
  uint8_t bytes[2]={reg,read ? 0 : data[0]};
  struct i2c_msg_s messages[2]={
    {.frequency=100000,.addr=dev->address,.flags=0,
     .buffer=read ? &reg : bytes,.length=read ? 1 : 2},
    {.frequency=100000,.addr=dev->address,.flags=I2C_M_READ,
     .buffer=data,.length=size}};
  if (sensor_get_timestamp()>=deadline) return -ETIMEDOUT;
  int ret=I2C_TRANSFER(dev->bus,messages,read ? 2 : 1);
  if (sensor_get_timestamp()>deadline) return -ETIMEDOUT;
  return ret<0 ? ret : 0;
}
static int wait_until(uint64_t end)
{
  for (;;) {
    uint64_t now=sensor_get_timestamp();
    if (now>=end) return 0;
    int ret=nxsig_usleep(end-now);
    if (ret<0 && ret!=-EINTR) return ret;
  }
}
static int activate(struct sensor_lowerhalf_s *lower,struct file *filep,bool enable)
{
  struct isl_device *dev=(struct isl_device *)lower;
  int ret=nxmutex_trylock(&dev->lock);
  if (ret<0) return ret;
  dev->active=enable;nxmutex_unlock(&dev->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct isl_device *dev=(struct isl_device *)lower;
  struct sensor_light sample;
  uint8_t data[2];
  uint64_t deadline=sensor_get_timestamp()+10000;
  int ret;
  if (!buffer || length<sizeof(sample)) return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);
  if (ret<0) return ret;
  if (!dev->active) {ret=-EACCES;goto done;}
  /* A conservative integration margin; the OS monotonic clock interpolates
   * SysTick. Recheck the deadline if a signal interrupts the sleep. */
  if ((ret=wait_until(sensor_get_timestamp()+2000))<0 ||
      (ret=transfer(dev,0x02,data,1,true,deadline))<0) goto done;
  memset(&sample,0,sizeof(sample));
  sample.timestamp=sensor_get_timestamp();
  sample.light=data[0]*4000.0f/256.0f;
  sample.ir=NAN;
  memcpy(buffer,&sample,sizeof(sample));ret=sizeof(sample);
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int isl29035_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  struct isl_device *dev;
  uint64_t deadline=sensor_get_timestamp()+100000;
  uint8_t id,value;
  int ret;
  if (!i2c || address!=0x44 || devno<0) return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if (!dev) return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  if ((ret=transfer(dev,0x0f,&id,1,true,deadline))<0) goto fail;
  if ((id&0x38)!=0x28) {ret=-ENODEV;goto fail;}
  value=id&0x7f;
  if ((ret=transfer(dev,0x0f,&value,1,false,deadline))<0) goto fail;
  value=0;
  if ((ret=transfer(dev,0x00,&value,1,false,deadline))<0) goto fail;
  value=0x09;
  if ((ret=transfer(dev,0x01,&value,1,false,deadline))<0) goto fail;
  value=0xa0;
  if ((ret=transfer(dev,0x00,&value,1,false,deadline))<0) goto fail;
  dev->lower.type=SENSOR_TYPE_LIGHT;dev->lower.ops=&ops;
  ret=sensor_register(&dev->lower,devno);
  if (ret==0) return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
