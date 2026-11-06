/* SPDX-License-Identifier: Apache-2.0
 * Private native New Sensor reference, AV02-4191EN (2015-11-13).
 * Raw proximity counts; no Legacy driver or gesture implementation dependency.
 */
#include <nuttx/config.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <nuttx/kmalloc.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/apds9960.h>
struct apds_device {
  struct sensor_lowerhalf_s lower;
  struct i2c_master_s *bus;
  uint8_t address;
  mutex_t lock;
  bool active;
};
static int transfer(struct apds_device *dev,uint8_t reg,uint8_t *value,bool read,uint64_t end)
{
  uint8_t bytes[2]={reg,read ? 0 : *value};
  struct i2c_msg_s messages[2]={
    {.frequency=100000,.addr=dev->address,.flags=0,.buffer=bytes,.length=read ? 1 : 2},
    {.frequency=100000,.addr=dev->address,.flags=I2C_M_READ,.buffer=value,.length=1}};
  if (sensor_get_timestamp()>=end) return -ETIMEDOUT;
  int ret=I2C_TRANSFER(dev->bus,messages,read ? 2 : 1);
  if (sensor_get_timestamp()>end) return -ETIMEDOUT;
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
  struct apds_device *dev=(struct apds_device *)lower;
  int ret=nxmutex_trylock(&dev->lock);
  if (ret<0) return ret;
  dev->active=enable;nxmutex_unlock(&dev->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct apds_device *dev=(struct apds_device *)lower;
  struct custom_proximity_raw sample;
  uint64_t end=sensor_get_timestamp()+100000;
  uint8_t status,value;
  int ret;
  if (!buffer || length<sizeof(sample)) return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);
  if (ret<0) return ret;
  if (!dev->active) {ret=-EACCES;goto done;}
  for (;;) {
    if ((ret=transfer(dev,0x93,&status,true,end))<0) goto done;
    if (status&2) break;
    uint64_t now=sensor_get_timestamp();
    /* Native sleep can round up across two 10 ms scheduler ticks. */
    if (now+30000>=end) {ret=-ETIMEDOUT;goto done;}
    if ((ret=wait_until(now+2000))<0) goto done;
  }
  if ((ret=transfer(dev,0x9c,&value,true,end))<0) goto done;
  memset(&sample,0,sizeof(sample));
  sample.timestamp=sensor_get_timestamp();sample.raw=value;
  memcpy(buffer,&sample,sizeof(sample));ret=sizeof(sample);
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int apds9960_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  const uint8_t settings[][2]={{0x80,0x00},{0x8d,0x60},{0x8e,0x47},
    {0x8f,0x04},{0x90,0x11},{0x9f,0x00},{0x80,0x05}};
  struct apds_device *dev;
  uint64_t end=sensor_get_timestamp()+100000;
  uint8_t value;
  char path[48];
  int ret;
  if (!i2c || address!=0x39 || devno<0) return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if (!dev) return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  if ((ret=wait_until(sensor_get_timestamp()+6000))<0 ||
      (ret=transfer(dev,0x92,&value,true,end))<0) goto fail;
  if (value!=0xab) {ret=-ENODEV;goto fail;}
  for (unsigned i=0;i<sizeof(settings)/sizeof(settings[0]);i++) {
    value=settings[i][1];
    if ((ret=transfer(dev,settings[i][0],&value,false,end))<0) goto fail;
  }
  dev->lower.type=SENSOR_TYPE_CUSTOM;dev->lower.ops=&ops;
  snprintf(path,sizeof(path),"/dev/uorb/proximity_raw%d",devno);
  ret=sensor_custom_register(&dev->lower,path,sizeof(struct custom_proximity_raw));
  if (ret==0) return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
