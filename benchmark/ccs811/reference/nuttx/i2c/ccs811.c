/* SPDX-License-Identifier: Apache-2.0
 * Private CCS811 reference. ams DS000459 v1-06, firmware2.0.0.
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
struct ccs_device;
struct ccs_node {
  struct sensor_lowerhalf_s lower;
  struct ccs_device *owner;
  unsigned channel;
  bool active;
};
struct ccs_device {
  struct i2c_master_s *bus;
  uint8_t address;
  mutex_t lock;
  struct ccs_node nodes[2];
};
static int transfer(struct ccs_device *dev,uint8_t reg,uint8_t *data,unsigned count,bool read,uint64_t end)
{
  uint8_t bytes[6]={reg};
  if(!read && count)memcpy(bytes+1,data,count);
  struct i2c_msg_s messages[2]={
    {.frequency=100000,.addr=dev->address,.flags=0,.buffer=bytes,.length=read?1:count+1},
    {.frequency=100000,.addr=dev->address,.flags=I2C_M_READ,.buffer=data,.length=count}};
  if(sensor_get_timestamp()>=end)return -ETIMEDOUT;
  int ret=I2C_TRANSFER(dev->bus,messages,read?2:1);
  if(sensor_get_timestamp()>end)return -ETIMEDOUT;
  return ret<0?ret:0;
}
static int check_status(struct ccs_device *dev,uint8_t *status,uint64_t end)
{
  int ret=transfer(dev,0x00,status,1,true,end);
  if(ret<0)return ret;
  if(*status&1){
    uint8_t error;
    ret=transfer(dev,0xe0,&error,1,true,end);
    return ret<0?ret:-EIO;
  }
  return 0;
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
  struct ccs_node *node=(struct ccs_node *)lower;
  int ret=nxmutex_trylock(&node->owner->lock);
  if (ret<0) return ret;
  node->active=enable;nxmutex_unlock(&node->owner->lock);return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct ccs_node *node=(struct ccs_node *)lower;
  struct ccs_device *dev=node->owner;
  union {struct sensor_co2 c;struct sensor_tvoc v;} sample;
  size_t size=node->channel?sizeof(sample.v):sizeof(sample.c);
  uint64_t end=sensor_get_timestamp()+2100000;
  uint8_t status,bytes[8];
  int ret;
  if(!buffer || length<size)return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  if(!node->active){ret=-EACCES;goto done;}
  for(;;){
    if((ret=check_status(dev,&status,end))<0)goto done;
    if((status&0x90)!=0x90){ret=-EIO;goto done;}
    if(status&8)break;
    uint64_t now=sensor_get_timestamp();
    if(now+40000>=end){ret=-ETIMEDOUT;goto done;}
    if((ret=wait_until(now+10000))<0)goto done;
  }
  if((ret=transfer(dev,0x02,bytes,node->channel?4:2,true,end))<0)goto done;
  unsigned offset=node->channel?2:0;
  uint16_t raw=((uint16_t)bytes[offset]<<8)|bytes[offset+1];
  if(node->channel ? raw>32768 : raw<400 || raw>29206){ret=-ERANGE;goto done;}
  memset(&sample,0,sizeof(sample));
  if(node->channel){sample.v.timestamp=sensor_get_timestamp();sample.v.tvoc=raw/1000.0f;}
  else {sample.c.timestamp=sensor_get_timestamp();sample.c.co2=raw;}
  memcpy(buffer,&sample,size);ret=size;
done:
  nxmutex_unlock(&dev->lock);return ret;
}

static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int ccs811_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  const int types[2]={SENSOR_TYPE_CO2,SENSOR_TYPE_TVOC};
  uint64_t end=sensor_get_timestamp()+100000;
  struct ccs_device *dev;
  uint8_t value;
  int ret;
  if (!i2c || address!=0x5a || devno<0) return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if (!dev) return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  if((ret=wait_until(sensor_get_timestamp()+20000))<0)goto fail;
  if((ret=transfer(dev,0x20,&value,1,true,end))<0)goto fail;
  if(value!=0x81){ret=-ENODEV;goto fail;}
  if((ret=check_status(dev,&value,end))<0)goto fail;
  if(!(value&0x10)){ret=-ENODEV;goto fail;}
  if(!(value&0x80)){
    if((ret=transfer(dev,0xf4,NULL,0,false,end))<0)goto fail;
    if((ret=wait_until(sensor_get_timestamp()+1000))<0)goto fail;
    if((ret=check_status(dev,&value,end))<0)goto fail;
    if((value&0x90)!=0x90){ret=-EIO;goto fail;}
  }
  value=0x10;if((ret=transfer(dev,0x01,&value,1,false,end))<0)goto fail;
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
