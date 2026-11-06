/* SPDX-License-Identifier: Apache-2.0
 * Private LPS22HB reference. ST DocID027083 Rev 6, pp. 37, 38, 45-46.
 * Native New Sensor implementation; no Legacy driver dependency.
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
struct lps_device;
struct lps_node {
  struct sensor_lowerhalf_s lower;
  struct lps_device *owner;
  unsigned channel;
  bool active;
};
struct lps_device {
  struct i2c_master_s *bus;
  uint8_t address;
  mutex_t lock;
  bool partial;
  struct lps_node nodes[2];
};
static int transfer(struct lps_device *dev,uint8_t reg,uint8_t *value,bool read,uint64_t end)
{
  uint8_t bytes[2]={reg,read ? 0 : *value};
  struct i2c_msg_s messages[2]={
    {.frequency=100000,.addr=dev->address,.flags=0,
     .buffer=bytes,.length=read ? 1 : 2},
    {.frequency=100000,.addr=dev->address,.flags=I2C_M_READ,
     .buffer=value,.length=1}};
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
  struct lps_node *node=(struct lps_node *)lower;
  int ret=nxmutex_trylock(&node->owner->lock);
  if (ret<0) return ret;
  node->active=enable;nxmutex_unlock(&node->owner->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct lps_node *node=(struct lps_node *)lower;
  struct lps_device *dev=node->owner;
  union {struct sensor_temp t;struct sensor_baro p;} sample;
  size_t size=node->channel ? sizeof(sample.p) : sizeof(sample.t);
  uint64_t end=sensor_get_timestamp()+2200000;
  uint8_t status,bytes[5];
  const uint8_t order[5]={0x2b,0x2c,0x28,0x29,0x2a};
  int ret;
  if (!buffer || length<size) return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);
  if (ret<0) return ret;
  if (!node->active) {ret=-EACCES;goto done;}
  /* A failed output sequence may have consumed T_DA without releasing BDU.
   * Release only a known interrupted acquisition; preserve unread ready data
   * on normal entry. Repeat recovery if the release transfer also fails. */
  if (dev->partial) {
    if ((ret=transfer(dev,0x2a,&status,true,end))<0) goto done;
    dev->partial=false;
  }
  for (;;) {
    if ((ret=transfer(dev,0x27,&status,true,end))<0) goto done;
    if ((status&3)==3) break;
    uint64_t now=sensor_get_timestamp();
    /* Reserve native watchdog tick rounding plus transaction margin. */
    if (now+30000>=end) {ret=-ETIMEDOUT;goto done;}
    if ((ret=wait_until(now+10000))<0) goto done;
  }
  dev->partial=true;
  for (unsigned i=0;i<5;i++)
    if ((ret=transfer(dev,order[i],&bytes[order[i]-0x28],true,end))<0) goto done;
  dev->partial=false;
  memset(&sample,0,sizeof(sample));
  if (node->channel) {
    int32_t raw=(uint32_t)bytes[0]|((uint32_t)bytes[1]<<8)|((uint32_t)bytes[2]<<16);
    if (raw&0x800000) raw-=0x1000000;
    sample.p.timestamp=sensor_get_timestamp();
    sample.p.pressure=raw/4096.0f;
    sample.p.temperature=NAN;
  } else {
    int16_t raw=(uint16_t)bytes[3]|((uint16_t)bytes[4]<<8);
    sample.t.timestamp=sensor_get_timestamp();
    sample.t.temperature=raw/100.0f;
  }
  memcpy(buffer,&sample,size);ret=size;
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int lps22hb_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  const int types[2]={SENSOR_TYPE_AMBIENT_TEMPERATURE,SENSOR_TYPE_BAROMETER};
  uint64_t end=sensor_get_timestamp()+100000;
  struct lps_device *dev;
  uint8_t value;
  int ret;
  if (!i2c || address!=0x5c || devno<0) return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if (!dev) return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  if ((ret=transfer(dev,0x0f,&value,true,end))<0) goto fail;
  if (value!=0xb1) {ret=-ENODEV;goto fail;}
  value=0;
  if ((ret=transfer(dev,0x11,&value,false,end))<0) goto fail;
  value=0x12;
  if ((ret=transfer(dev,0x10,&value,false,end))<0) goto fail;
  for (unsigned i=0;i<2;i++) {
    dev->nodes[i].owner=dev;dev->nodes[i].channel=i;
    dev->nodes[i].lower.type=types[i];dev->nodes[i].lower.ops=&ops;
    ret=sensor_register(&dev->nodes[i].lower,devno);
    if (ret<0) {
      while (i) sensor_unregister(&dev->nodes[--i].lower,devno);
      goto fail;
    }
  }
  return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
