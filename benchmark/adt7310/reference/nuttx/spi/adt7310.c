/* SPDX-License-Identifier: Apache-2.0
 * Hidden native New Sensor reference, based on frozen ADT7310 datasheet.
 * Not a Legacy wrapper, agent input, or independent scoring oracle.
 */
#include <nuttx/config.h>
#include <nuttx/kmalloc.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/spi/spi.h>
#include <errno.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
struct adt_device
{
  struct sensor_lowerhalf_s lower;
  struct spi_dev_s *spi;
  uint32_t devid;
  mutex_t lock;
  bool enabled, initialized;
};
static int transaction(struct adt_device *dev, uint8_t command, uint8_t value,
                       uint8_t *data, size_t n)
{
  uint8_t tx[3]={command,value,0}, rx[3];
  int result=SPI_LOCK(dev->spi,true);
  if (result < 0) return result;
  SPI_SETMODE(dev->spi,SPIDEV_MODE3);
  SPI_SETBITS(dev->spi,8);
  SPI_SETFREQUENCY(dev->spi,1000000);
  SPI_SELECT(dev->spi,dev->devid,true);
  SPI_EXCHANGE(dev->spi,tx,rx,n+1);
  SPI_SELECT(dev->spi,dev->devid,false);
  result=SPI_LOCK(dev->spi,false);
  if (result < 0) return result;
  if (data) memcpy(data,rx+1,n);
  return 0;
}
static int activate(struct sensor_lowerhalf_s *lower,struct file *filep,bool enable)
{
  struct adt_device *dev=(struct adt_device *)lower;
  int result=nxmutex_lock(&dev->lock);
  if (result < 0) return result;
  dev->enabled=enable;
  nxmutex_unlock(&dev->lock);
  return 0;
}
static int fetch(struct sensor_lowerhalf_s *lower,struct file *filep,char *buffer,size_t n)
{
  struct adt_device *dev=(struct adt_device *)lower;
  struct sensor_temp event;
  uint8_t bytes[2];
  if (!buffer || n<sizeof(event)) return -EINVAL;
  int result=nxmutex_lock(&dev->lock);
  if (result < 0) return result;
  if (!dev->enabled) {result=-EACCES;goto done;}
  if (!dev->initialized)
    {
      result=transaction(dev,0x58,0,bytes,1);
      if (result < 0) goto done;
      if ((bytes[0]&0xf8)!=0xc0) {result=-ENODEV;goto done;}
      result=transaction(dev,0x08,0x80,NULL,1);
      if (result < 0) goto done;
      dev->initialized=true;
    }
  uint64_t deadline=sensor_get_timestamp()+1000000;
  for (;;)
    {
      result=transaction(dev,0x40,0,bytes,1);
      if (result < 0) goto done;
      if (!(bytes[0]&0x80)) break;
      if (sensor_get_timestamp()>=deadline) {result=-ETIMEDOUT;goto done;}
      result=nxsig_usleep(10000);
      if (result < 0 && result!=-EINTR) goto done;
    }
  result=transaction(dev,0x50,0,bytes,2);
  if (result < 0) goto done;
  int16_t raw=(int16_t)(((uint16_t)bytes[0]<<8)|bytes[1]);
  memset(&event,0,sizeof(event));
  event.temperature=raw/128.0f;
  event.timestamp=sensor_get_timestamp();
  memcpy(buffer,&event,sizeof(event));
  result=sizeof(event);
done:
  nxmutex_unlock(&dev->lock);
  return result;
}
static const struct sensor_ops_s ops={.activate=activate,.fetch=fetch};
int adt7310_register(struct spi_dev_s *spi,uint32_t devid,int devno)
{
  if (!spi || devno<0) return -EINVAL;
  struct adt_device *dev=kmm_zalloc(sizeof(*dev));
  if (!dev) return -ENOMEM;
  dev->spi=spi;dev->devid=devid;dev->lower.type=SENSOR_TYPE_AMBIENT_TEMPERATURE;
  dev->lower.ops=&ops;nxmutex_init(&dev->lock);
  int result=sensor_register(&dev->lower,devno);
  if (result < 0) {nxmutex_destroy(&dev->lock);kmm_free(dev);}
  return result;
}
