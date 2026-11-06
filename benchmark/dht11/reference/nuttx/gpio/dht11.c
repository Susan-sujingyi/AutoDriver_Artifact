/* SPDX-License-Identifier: Apache-2.0
 * Hidden New Sensor reference. Device framing follows the frozen original
 * DHT11 datasheet; not an agent input and not the independent scoring oracle.
 */
#include <nuttx/config.h>
#include <nuttx/sensors/gpio_waveform.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/kmalloc.h>
#include <nuttx/mutex.h>
#include <nuttx/signal.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define SAMPLE_US 2
#define TRACE_BITS 3000
struct dht_device;
struct dht_node
{
  struct sensor_lowerhalf_s lower;
  struct dht_device *device;
  bool humidity;
  bool enabled;
};
struct dht_device
{
  struct dht_node nodes[2];
  mutex_t lock;
  uint64_t available;
  uint8_t trace[(TRACE_BITS+7)/8];
};

static int wait_until(uint64_t deadline)
{
  while (sensor_get_timestamp() < deadline)
    {
      uint64_t now = sensor_get_timestamp();
      if (now >= deadline) break;
      int result = nxsig_usleep((useconds_t)(deadline-now));
      if (result < 0 && result != -EINTR) return result;
    }
  return 0;
}

static bool bit(const uint8_t *trace, size_t position)
{
  return (trace[position/8] & (0x80u >> (position%8))) != 0;
}

static int transition(const uint8_t *trace, size_t *position, bool level)
{
  size_t start = *position;
  while (*position < TRACE_BITS && (*position-start)*SAMPLE_US < 150)
    {
      if (bit(trace,*position) == level) return 0;
      ++*position;
    }
  return -ETIMEDOUT;
}

static int decode(const uint8_t *trace, uint8_t frame[5])
{
  size_t pos = 0;
  memset(frame,0,5);
  const bool preamble[4] = {true,false,true,false};
  for (unsigned i=0;i<4;i++)
    if (transition(trace,&pos,preamble[i]) < 0) return -ETIMEDOUT;
  for (unsigned i=0;i<40;i++)
    {
      if (transition(trace,&pos,true) < 0) return -ETIMEDOUT;
      size_t rise = pos;
      if (transition(trace,&pos,false) < 0) return -ETIMEDOUT;
      frame[i/8] = (frame[i/8]<<1) | ((pos-rise)*SAMPLE_US >= 50);
    }
  if (transition(trace,&pos,true) < 0) return -ETIMEDOUT;
  for (;pos<TRACE_BITS;pos++)
    if (!bit(trace,pos)) return -EBADMSG;
  uint8_t checksum = frame[0]+frame[1]+frame[2]+frame[3];
  if (checksum != frame[4]) return -EBADMSG;
  return 0;
}

static int activate(struct sensor_lowerhalf_s *lower, struct file *filep, bool enable)
{
  struct dht_node *node = (struct dht_node *)lower;
  int result = nxmutex_lock(&node->device->lock);
  if (result < 0) return result;
  node->enabled = enable;
  nxmutex_unlock(&node->device->lock);
  return 0;
}

static int fetch(struct sensor_lowerhalf_s *lower, struct file *filep,
                 char *buffer, size_t length)
{
  struct dht_node *node = (struct dht_node *)lower;
  struct dht_device *dev = node->device;
  union {struct sensor_temp t; struct sensor_humi h;} sample;
  uint8_t frame[5];
  size_t size = node->humidity ? sizeof(sample.h) : sizeof(sample.t);
  if (!buffer || length < size) return -EINVAL;
  int result = nxmutex_lock(&dev->lock);
  if (result < 0) return result;
  bool started = false;
  if (!node->enabled) {result=-EACCES; goto done;}
  result=wait_until(dev->available);
  if (result < 0) goto done;
  started=true;
  result=gpio_waveform_drive_low(true);
  if (result < 0) goto done;
  result=wait_until(sensor_get_timestamp()+20000);
  if (result < 0) goto done;
  result=gpio_waveform_capture(dev->trace,sizeof(dev->trace),TRACE_BITS,SAMPLE_US);
  if (result < 0) goto done;
  if (result != TRACE_BITS) {result=-EIO; goto done;}
  result=decode(dev->trace,frame);
  if (result < 0) goto done;
  memset(&sample,0,sizeof(sample));
  if (node->humidity)
    {sample.h.timestamp=sensor_get_timestamp(); sample.h.humidity=frame[0];}
  else
    {sample.t.timestamp=sensor_get_timestamp(); sample.t.temperature=frame[2];}
  result=size;
done:
  if (started)
    {
      int release=gpio_waveform_drive_low(false);
      dev->available=sensor_get_timestamp()+1100000;
      if (release < 0 && result >= 0) result=release;
    }
  if (result >= 0) memcpy(buffer,&sample,size);
  nxmutex_unlock(&dev->lock);
  return result;
}

static const struct sensor_ops_s operations = {.activate=activate,.fetch=fetch};
int dht11_register(int devno)
{
  if (devno < 0) return -EINVAL;
  struct dht_device *dev=kmm_zalloc(sizeof(*dev));
  if (!dev) return -ENOMEM;
  nxmutex_init(&dev->lock);
  dev->available=sensor_get_timestamp()+1100000;
  for (unsigned i=0;i<2;i++)
    {
      dev->nodes[i].device=dev;
      dev->nodes[i].humidity=i!=0;
      dev->nodes[i].lower.type=i ? SENSOR_TYPE_RELATIVE_HUMIDITY : SENSOR_TYPE_AMBIENT_TEMPERATURE;
      dev->nodes[i].lower.ops=&operations;
      int result=sensor_register(&dev->nodes[i].lower,devno);
      if (result < 0)
        {
          while (i) sensor_unregister(&dev->nodes[--i].lower,devno);
          nxmutex_destroy(&dev->lock);
          kmm_free(dev);
          return result;
        }
    }
  return 0;
}
