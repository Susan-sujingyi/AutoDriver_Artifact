/* SPDX-License-Identifier: Apache-2.0
 * Private DHT20 reference. Native New Sensor; no Legacy driver dependency.
 * Numeric/frame basis: DHT20 V1.0 May2021. Recovery timing follows the
 * inherited contract's manufacturer-routine basis, tracked privately.
 */
#include <nuttx/config.h>
#include <nuttx/clock.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <nuttx/kmalloc.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/i2c/i2c_master.h>
struct dht_device;
struct dht_node {
  struct sensor_lowerhalf_s lower;
  struct dht_device *owner;
  unsigned channel;
  bool active;
};
struct dht_device {
  struct i2c_master_s *bus;
  uint8_t address;
  mutex_t lock;
  struct dht_node nodes[2];
};
static int wait_until(uint64_t end, uint64_t deadline)
{
  for (;;) {
    uint64_t now=sensor_get_timestamp();
    if (now>deadline) return -ETIMEDOUT;
    if (now>=end) return 0;
    /* Frozen NuttX rounds a relative sleep up to ticks and adds one tick
     * when arming the watchdog. Admit the sleep only while that scheduled
     * wakeup fits. Unbounded preemption is not bounded by this check. */
    uint64_t duration=end-now;
    uint64_t wake_budget=(USEC2TICK(duration)+1)*USEC_PER_TICK;
    if (wake_budget>deadline-now) return -ETIMEDOUT;
    int ret=nxsig_usleep(duration);
    if (ret<0 && ret!=-EINTR) return ret;
  }
}
static int transfer(struct dht_device *dev, uint8_t *bytes, int size,
                    bool read, uint64_t deadline)
{
  struct i2c_msg_s msg={.frequency=100000,.addr=dev->address,
                       .flags=read ? I2C_M_READ : 0,.buffer=bytes,.length=size};
  /* Address and payload each consume nine SCL periods, including ACK.
   * Do not start a transaction whose nominal wire time cannot fit. This
   * cannot bound arbitrary clock stretching or OS scheduling latency. */
  uint64_t now=sensor_get_timestamp();
  uint64_t wire_us=((uint64_t)(size+1)*9*1000000+msg.frequency-1)/msg.frequency;
  if (now>=deadline || wire_us>deadline-now) return -ETIMEDOUT;
  int ret=I2C_TRANSFER(dev->bus,&msg,1);
  if (sensor_get_timestamp()>deadline) return -ETIMEDOUT;
  return ret<0 ? ret : 0;
}
static uint8_t crc(const uint8_t *bytes)
{
  uint8_t result=0xff;
  for (unsigned i=0;i<6;i++) {
    result^=bytes[i];
    for (unsigned bit=0;bit<8;bit++)
      result=result&0x80 ? (result<<1)^0x31 : result<<1;
  }
  return result;
}
static int initialize(struct dht_device *dev)
{
  uint64_t deadline=sensor_get_timestamp()+600000;
  uint8_t status;
  const uint8_t registers[]={0x1b,0x1c,0x1e};
  int ret=wait_until(sensor_get_timestamp()+100000,deadline);
  if (ret<0 || (ret=transfer(dev,&status,1,true,deadline))<0) return ret;
  if ((status&0x18)!=0x18) {
    for (unsigned i=0;i<3;i++) {
      uint8_t data[3]={registers[i],0,0};
      if ((ret=transfer(dev,data,3,false,deadline))<0 ||
          (ret=wait_until(sensor_get_timestamp()+5000,deadline))<0 ||
          (ret=transfer(dev,data,3,true,deadline))<0 ||
          (ret=wait_until(sensor_get_timestamp()+10000,deadline))<0) return ret;
      data[0]=0xb0|registers[i];
      if ((ret=transfer(dev,data,3,false,deadline))<0) return ret;
    }
    if ((ret=wait_until(sensor_get_timestamp()+10000,deadline))<0 ||
        (ret=transfer(dev,&status,1,true,deadline))<0) return ret;
    if ((status&0x18)!=0x18) return -EIO;
  }
  ret=wait_until(sensor_get_timestamp()+10000,deadline);
  if (ret<0) return ret;
  return sensor_get_timestamp()<=deadline ? 0 : -ETIMEDOUT;
}
static int activate(struct sensor_lowerhalf_s *lower, struct file *filep, bool enable)
{
  struct dht_node *node=(struct dht_node *)lower;
  int ret=nxmutex_trylock(&node->owner->lock);
  if (ret<0) return ret;
  node->active=enable;
  nxmutex_unlock(&node->owner->lock);
  return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower, struct file *filep, char *buffer, size_t length)
{
  struct dht_node *node=(struct dht_node *)lower;
  struct dht_device *dev=node->owner;
  union {struct sensor_temp t;struct sensor_humi h;} sample;
  size_t size=node->channel ? sizeof(sample.h) : sizeof(sample.t);
  uint8_t data[7],command[3]={0xac,0x33,0};
  uint64_t deadline=sensor_get_timestamp()+250000;
  int ret;
  if (!buffer || length<size) return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);
  if (ret<0) return ret;
  if (!node->active) {ret=-EACCES;goto done;}
  if ((ret=transfer(dev,data,1,true,deadline))<0) goto done;
  /* A failed preceding fetch may have left a shot active. */
  if (!(data[0]&0x80)) {
    if ((ret=transfer(dev,command,3,false,deadline))<0 ||
        (ret=wait_until(sensor_get_timestamp()+80000,deadline))<0) goto done;
  }
  for (;;) {
    if ((ret=transfer(dev,data,7,true,deadline))<0) goto done;
    if (!(data[0]&0x80)) break;
    uint64_t next=sensor_get_timestamp()+10000;
    if (next>deadline) next=deadline;
    if ((ret=wait_until(next,deadline))<0) goto done;
  }
  if (crc(data)!=data[6]) {ret=-EBADMSG;goto done;}
  uint32_t rh=((uint32_t)data[1]<<12)|((uint32_t)data[2]<<4)|(data[3]>>4);
  uint32_t t=((uint32_t)(data[3]&15)<<16)|((uint32_t)data[4]<<8)|data[5];
  memset(&sample,0,sizeof(sample));
  if (node->channel) {
    sample.h.timestamp=sensor_get_timestamp();sample.h.humidity=rh*100.0f/1048576.0f;
  } else {
    sample.t.timestamp=sensor_get_timestamp();sample.t.temperature=t*200.0f/1048576.0f-50.0f;
  }
  memcpy(buffer,&sample,size);ret=size;
done:
  nxmutex_unlock(&dev->lock);
  return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int dht20_register(struct i2c_master_s *i2c, uint8_t address, int devno)
{
  const int types[2]={SENSOR_TYPE_AMBIENT_TEMPERATURE,SENSOR_TYPE_RELATIVE_HUMIDITY};
  struct dht_device *dev;
  if (!i2c || address!=0x38 || devno<0) return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));
  if (!dev) return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  int ret=initialize(dev);
  if (ret<0) {nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;}
  for (unsigned i=0;i<2;i++) {
    dev->nodes[i].owner=dev;dev->nodes[i].channel=i;
    dev->nodes[i].lower.type=types[i];dev->nodes[i].lower.ops=&ops;
    ret=sensor_register(&dev->nodes[i].lower,devno);
    if (ret<0) {
      while (i) sensor_unregister(&dev->nodes[--i].lower,devno);
      nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
    }
  }
  return 0;
}
