/* AutoDriver benchmark hidden reference control.
 * Independently implemented from the ROHM BH1750FVI datasheet's measurement
 * command, conversion time and measurement-result sections. No Legacy wrapper.
 * This file is private evaluation material, never an agent input.
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
#include <nuttx/sensors/bh1750.h>

struct bh1750_dev_s
{
  struct sensor_lowerhalf_s lower;
  struct i2c_master_s *bus;
  struct i2c_config_s config;
  mutex_t lock;
  bool active;
};

static int bh1750_activate(struct sensor_lowerhalf_s *lower,
                          struct file *filep, bool enable)
{
  struct bh1750_dev_s *dev = (struct bh1750_dev_s *)lower;
  int ret = nxmutex_lock(&dev->lock);
  if (ret < 0)
    {
      return ret;
    }

  dev->active = enable;
  nxmutex_unlock(&dev->lock);
  return 0;
}

static int bh1750_fetch(struct sensor_lowerhalf_s *lower, struct file *filep,
                       char *buffer, size_t buflen)
{
  struct bh1750_dev_s *dev = (struct bh1750_dev_s *)lower;
  struct sensor_light sample;
  uint8_t command = 0x20;
  uint8_t bytes[2];
  int ret;

  if (buffer == NULL || buflen < sizeof(sample))
    {
      return -EINVAL;
    }

  ret = nxmutex_lock(&dev->lock);
  if (ret < 0)
    {
      return ret;
    }

  if (!dev->active)
    {
      ret = -EACCES;
      goto out;
    }

  ret = i2c_write(dev->bus, &dev->config, &command, 1);
  if (ret < 0)
    {
      goto out;
    }

  /* Use a monotonic deadline: interrupted sleep must not shorten conversion. */
  uint64_t deadline = sensor_get_timestamp() + 180000;
  for (;;)
    {
      uint64_t now = sensor_get_timestamp();
      if (now >= deadline)
        {
          break;
        }

      ret = nxsig_usleep((useconds_t)(deadline - now));
      if (ret < 0 && ret != -EINTR)
        {
          goto out;
        }
    }

  ret = i2c_read(dev->bus, &dev->config, bytes, sizeof(bytes));
  if (ret < 0)
    {
      goto out;
    }

  memset(&sample, 0, sizeof(sample));
  sample.timestamp = sensor_get_timestamp();
  sample.light = ((uint16_t)bytes[0] << 8 | bytes[1]) / 1.2f;
  sample.ir = NAN;
  memcpy(buffer, &sample, sizeof(sample));
  ret = sizeof(sample);
out:
  nxmutex_unlock(&dev->lock);
  return ret;
}

static const struct sensor_ops_s g_bh1750_ops =
{
  .activate = bh1750_activate,
  .fetch = bh1750_fetch,
};

int bh1750_register(struct i2c_master_s *i2c, uint8_t address, int devno)
{
  struct bh1750_dev_s *dev;
  int ret;
  if (i2c == NULL || address > 0x7f || devno < 0)
    {
      return -EINVAL;
    }

  dev = kmm_zalloc(sizeof(*dev));
  if (dev == NULL)
    {
      return -ENOMEM;
    }

  nxmutex_init(&dev->lock);
  dev->bus = i2c;
  dev->config.address = address;
  dev->config.addrlen = 7;
  dev->config.frequency = 100000;
  dev->lower.type = SENSOR_TYPE_LIGHT;
  dev->lower.ops = &g_bh1750_ops;
  ret = sensor_register(&dev->lower, devno);
  if (ret < 0)
    {
      nxmutex_destroy(&dev->lock);
      kmm_free(dev);
    }

  return ret;
}
