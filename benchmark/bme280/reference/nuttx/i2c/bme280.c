/* SPDX-License-Identifier: Apache-2.0
 * Private autodriver benchmark reference; never an agent input.
 * Device protocol and floating-point compensation follow Bosch BME280
 * datasheet BST-BME280-DS002, sections 3.3, 4.2 and 5.4. This is a native
 * New Sensor lower half, not a wrapper around a Legacy driver.
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

struct bme_device;
struct bme_node
{
  struct sensor_lowerhalf_s lower;
  struct bme_device *owner;
  unsigned int channel;
  bool active;
};

struct bme_calibration
{
  uint16_t t1;
  int16_t t2, t3;
  uint16_t p1;
  int16_t p2, p3, p4, p5, p6, p7, p8, p9;
  uint8_t h1, h3;
  int16_t h2, h4, h5;
  int8_t h6;
};

struct bme_device
{
  struct i2c_master_s *i2c;
  uint8_t address;
  mutex_t lock;
  struct bme_node nodes[3];
  struct bme_calibration calibration;
  bool initialized;
};

static uint16_t le16(const uint8_t *data)
{
  return (uint16_t)data[0] | ((uint16_t)data[1] << 8);
}

static int16_t signed12(unsigned int value)
{
  return value & 0x800 ? (int16_t)((int)value - 4096) : (int16_t)value;
}

static int wait_us(uint32_t interval)
{
  uint64_t deadline = sensor_get_timestamp() + interval;
  for (;;)
    {
      uint64_t now = sensor_get_timestamp();
      int result;
      if (now >= deadline)
        {
          return 0;
        }

      result = nxsig_usleep((useconds_t)(deadline - now));
      if (result < 0 && result != -EINTR)
        {
          return result;
        }
    }
}

static int transfer(struct bme_device *device, uint8_t reg,
                    uint8_t *data, size_t count, bool read)
{
  uint8_t tx[27];
  struct i2c_msg_s messages[2];
  if (!count || count > sizeof(tx)-1) return -EINVAL;
  tx[0]=reg;
  messages[0].frequency=100000;
  messages[0].addr=device->address;
  messages[0].flags=0;
  messages[0].buffer=tx;
  messages[0].length=read ? 1 : count+1;
  if (read)
    {
      messages[1].frequency=100000;
      messages[1].addr=device->address;
      messages[1].flags=I2C_M_READ;
      messages[1].buffer=data;
      messages[1].length=count;
    }
  else memcpy(tx+1,data,count);
  int result=I2C_TRANSFER(device->i2c,messages,read ? 2 : 1);
  return result < 0 ? result : 0;
}

static int write_reg(struct bme_device *device, uint8_t reg, uint8_t value)
{
  return transfer(device, reg, &value, 1, false);
}

static int wait_status(struct bme_device *device, uint8_t mask, uint32_t timeout)
{
  uint64_t deadline = sensor_get_timestamp() + timeout;
  for (;;)
    {
      uint8_t status;
      int result = transfer(device, 0xf3, &status, 1, true);
      if (result < 0)
        {
          return result;
        }

      if (!(status & mask))
        {
          return 0;
        }

      if (sensor_get_timestamp() >= deadline)
        {
          return -ETIMEDOUT;
        }

      result = wait_us(5000);
      if (result < 0)
        {
          return result;
        }
    }
}

static int initialize(struct bme_device *device)
{
  struct bme_calibration *c = &device->calibration;
  uint8_t id;
  uint8_t tp[26];
  uint8_t h[7];
  int result = transfer(device, 0xd0, &id, 1, true);
  if (result < 0)
    {
      return result;
    }

  if (id != 0x60)
    {
      return -ENODEV;
    }

  result = write_reg(device, 0xe0, 0xb6);
  if (result < 0 || (result = wait_us(3000)) < 0 ||
      (result = wait_status(device, 1, 100000)) < 0 ||
      (result = transfer(device, 0x88, tp, sizeof(tp), true)) < 0 ||
      (result = transfer(device, 0xe1, h, sizeof(h), true)) < 0)
    {
      return result;
    }

  c->t1 = le16(tp);
  c->t2 = (int16_t)le16(tp + 2);
  c->t3 = (int16_t)le16(tp + 4);
  c->p1 = le16(tp + 6);
  c->p2 = (int16_t)le16(tp + 8);
  c->p3 = (int16_t)le16(tp + 10);
  c->p4 = (int16_t)le16(tp + 12);
  c->p5 = (int16_t)le16(tp + 14);
  c->p6 = (int16_t)le16(tp + 16);
  c->p7 = (int16_t)le16(tp + 18);
  c->p8 = (int16_t)le16(tp + 20);
  c->p9 = (int16_t)le16(tp + 22);
  c->h1 = tp[25];
  c->h2 = (int16_t)le16(h);
  c->h3 = h[2];
  c->h4 = signed12(((unsigned int)h[3] << 4) | (h[4] & 0xf));
  c->h5 = signed12(((unsigned int)h[5] << 4) | (h[4] >> 4));
  c->h6 = (int8_t)h[6];
  result = write_reg(device, 0xf5, 0);
  if (result == 0)
    {
      result = write_reg(device, 0xf2, 1);
    }

  device->initialized = result == 0;
  return result;
}

static double temperature(const struct bme_calibration *c, uint32_t adc, double *fine)
{
  double a = ((double)adc / 16384.0 - c->t1 / 1024.0) * c->t2;
  double b = (double)adc / 131072.0 - c->t1 / 8192.0;
  *fine = a + b * b * c->t3;
  return *fine / 5120.0;
}

static int pressure(const struct bme_calibration *c, uint32_t adc, double fine, float *hpa)
{
  double a = fine / 2.0 - 64000.0;
  double b = a * a * c->p6 / 32768.0 + a * c->p5 * 2.0;
  double p;
  b = b / 4.0 + c->p4 * 65536.0;
  a = (c->p3 * a * a / 524288.0 + c->p2 * a) / 524288.0;
  a = (1.0 + a / 32768.0) * c->p1;
  if (a == 0.0)
    {
      return -ERANGE;
    }

  p = (1048576.0 - adc - b / 4096.0) * 6250.0 / a;
  a = c->p9 * p * p / 2147483648.0;
  b = p * c->p8 / 32768.0;
  *hpa = (p + (a + b + c->p7) / 16.0) / 100.0;
  return isfinite(*hpa) && *hpa >= 0 ? 0 : -ERANGE;
}

static float humidity(const struct bme_calibration *c, uint16_t adc, double fine)
{
  double value = fine - 76800.0;
  value = (adc - (c->h4 * 64.0 + c->h5 / 16384.0 * value)) *
          (c->h2 / 65536.0 * (1.0 + c->h6 / 67108864.0 * value *
                             (1.0 + c->h3 / 67108864.0 * value)));
  value *= 1.0 - c->h1 * value / 524288.0;
  return value < 0 ? 0 : value > 100 ? 100 : (float)value;
}

static int activate(struct sensor_lowerhalf_s *lower, struct file *filep, bool enable)
{
  struct bme_node *node = (struct bme_node *)lower;
  int result = nxmutex_lock(&node->owner->lock);
  if (result < 0)
    {
      return result;
    }

  node->active = enable;
  nxmutex_unlock(&node->owner->lock);
  return 0;
}

static int fetch(struct sensor_lowerhalf_s *lower, struct file *filep, char *buffer, size_t length)
{
  struct bme_node *node = (struct bme_node *)lower;
  struct bme_device *device = node->owner;
  union
  {
    struct sensor_temp temp;
    struct sensor_humi humi;
    struct sensor_baro baro;
  } sample;
  uint8_t data[8];
  double fine, degrees;
  uint32_t adc_t, adc_p;
  uint16_t adc_h;
  uint64_t timestamp;
  size_t size = node->channel == 0 ? sizeof(sample.temp) :
                node->channel == 1 ? sizeof(sample.humi) : sizeof(sample.baro);
  int result;
  if (buffer == NULL || length < size)
    {
      return -EINVAL;
    }

  result = nxmutex_lock(&device->lock);
  if (result < 0)
    {
      return result;
    }

  if (!node->active)
    {
      result = -EACCES;
      goto done;
    }

  if (!device->initialized && (result = initialize(device)) < 0)
    {
      goto done;
    }

  result = write_reg(device, 0xf4, 0x35);
  if (result < 0 || (result = wait_us(50000)) < 0 ||
      (result = wait_status(device, 8, 800000)) < 0 ||
      (result = transfer(device, 0xf7, data, sizeof(data), true)) < 0)
    {
      goto done;
    }

  adc_p = (uint32_t)data[0] << 12 | (uint32_t)data[1] << 4 | data[2] >> 4;
  adc_t = (uint32_t)data[3] << 12 | (uint32_t)data[4] << 4 | data[5] >> 4;
  adc_h = (uint16_t)data[6] << 8 | data[7];
  if (adc_t == 0x80000 || adc_p == 0x80000 || adc_h == 0x8000)
    {
      result = -EIO;
      goto done;
    }

  degrees = temperature(&device->calibration, adc_t, &fine);
  fine = (int32_t)fine; /* Datasheet Appendix A shared t_fine representation. */
  timestamp = sensor_get_timestamp();
  memset(&sample, 0, sizeof(sample));
  if (node->channel == 0)
    {
      sample.temp.timestamp = timestamp;
      sample.temp.temperature = degrees;
    }
  else if (node->channel == 1)
    {
      sample.humi.timestamp = timestamp;
      sample.humi.humidity = humidity(&device->calibration, adc_h, fine);
    }
  else
    {
      sample.baro.timestamp = timestamp;
      sample.baro.temperature = degrees;
      result = pressure(&device->calibration, adc_p, fine, &sample.baro.pressure);
      if (result < 0)
        {
          goto done;
        }
    }

  memcpy(buffer, &sample, size);
  result = size;
done:
  nxmutex_unlock(&device->lock);
  return result;
}

static const struct sensor_ops_s operations = {.activate = activate, .fetch = fetch};

int bme280_register(struct i2c_master_s *i2c, uint8_t address, int devno)
{
  static const int types[3] = {SENSOR_TYPE_AMBIENT_TEMPERATURE,
                              SENSOR_TYPE_RELATIVE_HUMIDITY, SENSOR_TYPE_BAROMETER};
  struct bme_device *device;
  unsigned int i;
  int result;
  if (i2c == NULL || address > 0x7f || devno < 0)
    {
      return -EINVAL;
    }

  device = kmm_zalloc(sizeof(*device));
  if (device == NULL)
    {
      return -ENOMEM;
    }

  device->i2c = i2c;
  device->address = address;
  nxmutex_init(&device->lock);
  for (i = 0; i < 3; i++)
    {
      device->nodes[i].owner = device;
      device->nodes[i].channel = i;
      device->nodes[i].lower.type = types[i];
      device->nodes[i].lower.ops = &operations;
      result = sensor_register(&device->nodes[i].lower, devno);
      if (result < 0)
        {
          while (i)
            {
              sensor_unregister(&device->nodes[--i].lower, devno);
            }

          nxmutex_destroy(&device->lock);
          kmm_free(device);
          return result;
        }
    }

  return 0;
}
