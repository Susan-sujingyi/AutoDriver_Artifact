/* SPDX-License-Identifier: Apache-2.0
 * Private benchmark reference, not an agent input.
 * SHT3x-DIS v7 sections 2.2, 4.3, 4.4, 4.12, 4.13.
 * Native New Sensor lower half; no Legacy implementation dependency.
 */
#include <nuttx/config.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <nuttx/kmalloc.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/i2c/i2c_master.h>

struct sht_device;
struct sht_node
{
  struct sensor_lowerhalf_s lower;
  struct sht_device *owner;
  unsigned channel;
  bool active;
};
struct sht_device
{
  struct i2c_master_s *bus;
  uint8_t address;
  mutex_t lock;
  uint64_t available;
  struct sht_node nodes[2];
};
static int wait_until(uint64_t deadline)
{
  for (;;)
    {
      uint64_t now=sensor_get_timestamp();
      if (now >= deadline) return 0;
      int ret=nxsig_usleep(deadline-now);
      if (ret < 0 && ret != -EINTR) return ret;
    }
}
static int transfer(struct sht_device *dev, uint8_t *data, int length, bool read)
{
  struct i2c_msg_s msg={.frequency=100000,.addr=dev->address,
                        .flags=read ? I2C_M_READ : 0,.buffer=data,.length=length};
  int ret=I2C_TRANSFER(dev->bus,&msg,1);
  return ret < 0 ? ret : 0;
}
static uint8_t crc(const uint8_t *data)
{
  uint8_t value=0xff;
  for (unsigned i=0;i<2;i++)
    {
      value^=data[i];
      for (unsigned bit=0;bit<8;bit++)
        value=value&0x80 ? (value<<1)^0x31 : value<<1;
    }
  return value;
}
static int activate(struct sensor_lowerhalf_s *lower, struct file *filep, bool enable)
{
  struct sht_node *node=(struct sht_node *)lower;
  int ret=nxmutex_lock(&node->owner->lock);
  if (ret < 0) return ret;
  node->active=enable;
  nxmutex_unlock(&node->owner->lock);
  return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower, struct file *filep, char *buffer, size_t length)
{
  struct sht_node *node=(struct sht_node *)lower;
  struct sht_device *dev=node->owner;
  union {struct sensor_temp t; struct sensor_humi h;} sample;
  size_t size=node->channel ? sizeof(sample.h) : sizeof(sample.t);
  uint8_t command[2]={0x24,0x00},data[6];
  uint64_t deadline=sensor_get_timestamp()+100000;
  uint16_t raw;
  unsigned offset=node->channel ? 3 : 0;
  int ret;
  if (!buffer || length < size) return -EINVAL;
  ret=nxmutex_lock(&dev->lock);
  if (ret < 0) return ret;
  if (!node->active) {ret=-EACCES;goto done;}
  if ((ret=wait_until(dev->available)) < 0 ||
      (ret=transfer(dev,command,2,false)) < 0 ||
      (ret=wait_until(sensor_get_timestamp()+16000)) < 0) goto done;
  for (;;)
    {
      ret=transfer(dev,data,node->channel ? 6 : 3,true);
      if (ret == 0) break;
      if (ret != -ENXIO && ret != -EAGAIN) goto done;
      if (sensor_get_timestamp() >= deadline) {ret=-ETIMEDOUT;goto done;}
      uint64_t next=sensor_get_timestamp()+1000;
      if ((ret=wait_until(next < deadline ? next : deadline)) < 0) goto done;
    }
  if (crc(data+offset) != data[offset+2]) {ret=-EBADMSG;goto done;}
  raw=((uint16_t)data[offset]<<8)|data[offset+1];
  memset(&sample,0,sizeof(sample));
  if (node->channel)
    {
      sample.h.timestamp=sensor_get_timestamp();
      sample.h.humidity=100.0f*raw/65535.0f;
    }
  else
    {
      sample.t.timestamp=sensor_get_timestamp();
      sample.t.temperature=-45.0f+175.0f*raw/65535.0f;
    }
  memcpy(buffer,&sample,size);
  ret=size;
done:
  nxmutex_unlock(&dev->lock);
  return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int sht3x_register(struct i2c_master_s *i2c, uint8_t address, int devno)
{
  const int types[2]={SENSOR_TYPE_AMBIENT_TEMPERATURE,SENSOR_TYPE_RELATIVE_HUMIDITY};
  struct sht_device *dev;
  if (!i2c || address > 0x7f || devno < 0) return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));
  if (!dev) return -ENOMEM;
  dev->bus=i2c;dev->address=address;
  nxmutex_init(&dev->lock);
  dev->available=sensor_get_timestamp()+2000;
  for (unsigned i=0;i<2;i++)
    {
      dev->nodes[i].owner=dev;dev->nodes[i].channel=i;
      dev->nodes[i].lower.type=types[i];dev->nodes[i].lower.ops=&ops;
      int ret=sensor_register(&dev->nodes[i].lower,devno);
      if (ret < 0)
        {
          while (i) sensor_unregister(&dev->nodes[--i].lower,devno);
          nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
        }
    }
  return 0;
}
