/* SPDX-License-Identifier: Apache-2.0
 * Private BMP388 reference. Bosch BST-BMP388-DS001-07 polynomial; floating native values.
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
struct bmp_device;
struct bmp_node {
  struct sensor_lowerhalf_s lower;
  struct bmp_device *owner;
  unsigned channel;
  bool active;
};
struct bmp_device {
  struct i2c_master_s *bus;
  uint8_t address;
  mutex_t lock;
  double q[14];
  struct bmp_node nodes[2];
};
static int transfer(struct bmp_device *dev,uint8_t reg,uint8_t *data,unsigned count,bool read,uint64_t end)
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
  struct bmp_node *node=(struct bmp_node *)lower;
  int ret=nxmutex_trylock(&node->owner->lock);
  if (ret<0) return ret;
  node->active=enable;nxmutex_unlock(&node->owner->lock);return 0;
}
static unsigned u16le(const uint8_t *b){return b[0]|((unsigned)b[1]<<8);}
static int s16le(const uint8_t *b){unsigned v=u16le(b);return v>=32768?(int)v-65536:v;}
static int s8(uint8_t b){return b>=128?(int)b-256:b;}
static void calibration(struct bmp_device *dev,const uint8_t *b)
{
  double *q=dev->q;
  q[0]=u16le(b)*256.0;q[1]=u16le(b+2)/1073741824.0;q[2]=s8(b[4])/281474976710656.0;
  q[3]=(s16le(b+5)-16384)/1048576.0;q[4]=(s16le(b+7)-16384)/536870912.0;
  q[5]=s8(b[9])/4294967296.0;q[6]=s8(b[10])/137438953472.0;
  q[7]=u16le(b+11)*8.0;q[8]=u16le(b+13)/64.0;q[9]=s8(b[15])/256.0;
  q[10]=s8(b[16])/32768.0;q[11]=s16le(b+17)/281474976710656.0;
  q[12]=s8(b[19])/281474976710656.0;q[13]=s8(b[20])/36893488147419103232.0;
}
static unsigned u24le(const uint8_t *b){return b[0]|((unsigned)b[1]<<8)|((unsigned)b[2]<<16);}
static double temperature(struct bmp_device *dev,const uint8_t *b)
{
  double d=u24le(b)-dev->q[0];return d*(dev->q[1]+d*dev->q[2]);
}
static double pressure(struct bmp_device *dev,const uint8_t *b,double t)
{
  const double *q=dev->q;double u=u24le(b);
  double offset=q[7]+t*(q[8]+t*(q[9]+t*q[10]));
  double slope=q[3]+t*(q[4]+t*(q[5]+t*q[6]));
  return (offset+u*slope+u*u*(q[11]+t*q[12])+u*u*u*q[13])/100.0;
}
static int errors(struct bmp_device *dev,uint64_t end)
{
  uint8_t value;int ret=transfer(dev,0x02,&value,1,true,end);
  return ret<0?ret:(value&7)?-EIO:0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t length)
{
  struct bmp_node *node=(struct bmp_node *)lower;struct bmp_device *dev=node->owner;
  union {struct sensor_temp t;struct sensor_baro p;} sample;
  size_t size=node->channel?sizeof(sample.p):sizeof(sample.t);
  uint64_t end=sensor_get_timestamp()+100000;
  uint8_t status,bytes[6];int ret;
  if(!buffer || length<size)return -EINVAL;
  ret=nxmutex_trylock(&dev->lock);if(ret<0)return ret;
  if(!node->active){ret=-EACCES;goto done;}
  for(;;){
    if((ret=errors(dev,end))<0)goto done;
    if((ret=transfer(dev,0x03,&status,1,true,end))<0)goto done;
    if(status&(node->channel?0x20:0x40))break;
    uint64_t now=sensor_get_timestamp();
    if(now+30000>=end){ret=-ETIMEDOUT;goto done;}
    if((ret=wait_until(now+1000))<0)goto done;
  }
  if((ret=transfer(dev,node->channel?0x04:0x07,bytes,node->channel?6:3,true,end))<0)goto done;
  double t=temperature(dev,bytes+(node->channel?3:0));
  memset(&sample,0,sizeof(sample));
  if(node->channel){sample.p.timestamp=sensor_get_timestamp();sample.p.pressure=pressure(dev,bytes,t);sample.p.temperature=NAN;}
  else {sample.t.timestamp=sensor_get_timestamp();sample.t.temperature=t;}
  memcpy(buffer,&sample,size);ret=size;
done:
  nxmutex_unlock(&dev->lock);return ret;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int bmp388_register(struct i2c_master_s *i2c,uint8_t address,int devno)
{
  const int types[2]={SENSOR_TYPE_AMBIENT_TEMPERATURE,SENSOR_TYPE_BAROMETER};
  uint64_t end=sensor_get_timestamp()+100000;
  struct bmp_device *dev;uint8_t value,trim[21];int ret;
  if(!i2c || address!=0x76 || devno<0)return -EINVAL;
  dev=kmm_zalloc(sizeof(*dev));if(!dev)return -ENOMEM;
  dev->bus=i2c;dev->address=address;nxmutex_init(&dev->lock);
  if((ret=wait_until(sensor_get_timestamp()+2000))<0)goto fail;
  for(;;){
    if((ret=transfer(dev,0x03,&value,1,true,end))<0)goto fail;
    if(value&0x10)break;
  }
  value=0xb6;if((ret=transfer(dev,0x7e,&value,1,false,end))<0)goto fail;
  if((ret=wait_until(sensor_get_timestamp()+2000))<0)goto fail;
  if((ret=transfer(dev,0x00,&value,1,true,end))<0)goto fail;
  if(value!=0x50){ret=-ENODEV;goto fail;}
  if((ret=transfer(dev,0x31,trim,21,true,end))<0)goto fail;
  calibration(dev,trim);
  const uint8_t config[][2]={{0x1d,3},{0x1c,2},{0x1f,0},{0x1b,0x33}};
  for(unsigned i=0;i<sizeof(config)/sizeof(config[0]);i++){
    value=config[i][1];if((ret=transfer(dev,config[i][0],&value,1,false,end))<0)goto fail;
  }
  if((ret=errors(dev,end))<0)goto fail;
  for(unsigned i=0;i<2;i++){
    dev->nodes[i].owner=dev;dev->nodes[i].channel=i;
    dev->nodes[i].lower.type=types[i];dev->nodes[i].lower.ops=&ops;
    ret=sensor_register(&dev->nodes[i].lower,devno);
    if(ret<0){while(i)sensor_unregister(&dev->nodes[--i].lower,devno);goto fail;}
  }
  return 0;
fail:
  nxmutex_destroy(&dev->lock);kmm_free(dev);return ret;
}
