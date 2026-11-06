#include <assert.h>
#include <errno.h>
#include <string.h>
#include "bus.h"

/* Only hardware peripherals advance the physical clock directly. OS sleeps
 * go through the real watchdog/scheduler and SIM idle timer interrupt. */
extern uint64_t host_gettime(bool rtc);
extern void host_sleep(uint64_t nsec);

uint64_t bench_now_us(void)
{
  return host_gettime(false) / 1000;
}

void bench_wire_delay(unsigned bits, uint32_t frequency)
{
  assert(frequency > 0);
  host_sleep(((uint64_t)bits * 1000000000ull + frequency - 1) / frequency);
}

static int spi_lock(struct spi_dev_s *dev, bool acquire)
{
  struct bench_spi *bus = (struct bench_spi *)dev;
  if (acquire)
    {
      if (bus->next_lock_error)
        {
          int result = bus->next_lock_error;
          bus->next_lock_error = 0;
          return result;
        }
      return nxmutex_lock(&bus->mutex);
    }
  assert(!bus->selected && nxmutex_is_hold(&bus->mutex));
  return nxmutex_unlock(&bus->mutex);
}

static void spi_select(struct spi_dev_s *dev, uint32_t id, bool selected)
{
  struct bench_spi *bus = (struct bench_spi *)dev;
  assert(nxmutex_is_hold(&bus->mutex) && id == bus->device_id);
  /* Reasserting an existing CS level has no electrical edge. */
  if (bus->selected != selected)
    {
      bus->selected = selected;
      bus->selections += selected;
      bus->device->select(bus->context, selected);
    }
}

static uint32_t spi_frequency(struct spi_dev_s *dev, uint32_t frequency)
{
  struct bench_spi *bus = (struct bench_spi *)dev;
  assert(nxmutex_is_hold(&bus->mutex));
  bus->frequency = frequency;
  return frequency;
}

static void spi_mode(struct spi_dev_s *dev, enum spi_mode_e mode)
{
  struct bench_spi *bus = (struct bench_spi *)dev;
  assert(nxmutex_is_hold(&bus->mutex));
  bus->mode = mode;
}

static void spi_bits(struct spi_dev_s *dev, int bits)
{
  struct bench_spi *bus = (struct bench_spi *)dev;
  assert(nxmutex_is_hold(&bus->mutex));
  bus->bits = bits;
}

static uint32_t spi_send(struct spi_dev_s *dev, uint32_t value)
{
  struct bench_spi *bus = (struct bench_spi *)dev;
  assert(nxmutex_is_hold(&bus->mutex) && bus->selected);
  assert(bus->bits == 8 && bus->mode == bus->required_mode);
  assert(bus->frequency > 0 && bus->frequency <= bus->max_frequency);
  bench_wire_delay(8, bus->frequency);
  bus->words++;
  return bus->device->exchange(bus->context, (uint8_t)value);
}

static void spi_exchange(struct spi_dev_s *dev, const void *tx, void *rx,
                         size_t words)
{
  const uint8_t *send = tx;
  uint8_t *receive = rx;
  for (size_t i = 0; i < words; i++)
    {
      uint8_t value = spi_send(dev, send ? send[i] : 0xff);
      if (receive)
        {
          receive[i] = value;
        }
    }
}

static uint8_t spi_status(struct spi_dev_s *dev, uint32_t id)
{
  return id == ((struct bench_spi *)dev)->device_id ? SPI_STATUS_PRESENT : 0;
}

static int spi_cmddata(struct spi_dev_s *dev, uint32_t id, bool command)
{
  /* These sensor tasks have no separate command/data pin. */
  return -ENOSYS;
}

static const struct spi_ops_s spi_ops =
{
  .lock = spi_lock, .select = spi_select, .setfrequency = spi_frequency,
  .setmode = spi_mode, .setbits = spi_bits, .status = spi_status,
  .cmddata = spi_cmddata, .send = spi_send, .exchange = spi_exchange
};

void bench_spi_init(struct bench_spi *bus, const struct bench_spi_device *device,
                    void *context, uint32_t id, enum spi_mode_e mode,
                    uint32_t max_frequency)
{
  memset(bus, 0, sizeof(*bus));
  bus->dev.ops = &spi_ops;
  nxmutex_init(&bus->mutex);
  bus->device = device;
  bus->context = context;
  bus->device_id = id;
  bus->required_mode = mode;
  bus->max_frequency = max_frequency;
  bus->bits = 8;
  bus->mode = SPIDEV_MODE0;
}

static int i2c_transfer(struct i2c_master_s *dev, struct i2c_msg_s *msgs,
                        int count)
{
  struct bench_i2c *bus = (struct bench_i2c *)dev;
  int result = nxmutex_lock(&bus->mutex);
  if (result < 0)
    {
      if (bus->device->transfer_failed)
        bus->device->transfer_failed(bus->context, msgs, count, result);
      return result;
    }
  if (bus->next_transfer_error)
    {
      result = bus->next_transfer_error;
      bus->next_transfer_error = 0;
      goto out;
    }
  assert(msgs && count > 0);
  for (int i = 0; i < count; i++)
    {
      struct i2c_msg_s *msg = &msgs[i];
      bool start = !(msg->flags & I2C_M_NOSTART);
      bool repeated = start && bus->active;
      bool read = msg->flags & I2C_M_READ;
      bool stop = !(msg->flags & I2C_M_NOSTOP) &&
                   !(i + 1 < count && (msgs[i + 1].flags & I2C_M_NOSTART));
      assert(!(msg->flags & ~(I2C_M_READ | I2C_M_TEN |
                              I2C_M_NOSTOP | I2C_M_NOSTART)));
      assert(msg->length >= 0 && (msg->buffer || !msg->length));
      assert(msg->frequency > 0 && msg->frequency <= bus->max_frequency);
      if (!start)
        {
          assert(bus->active && bus->address == msg->addr && bus->reading == read);
        }
      else
        {
          bus->active = true;
          bus->address = msg->addr;
          bus->reading = read;
        }
      bus->messages++;
      if (start)
        {
          bench_wire_delay(9, msg->frequency);
          if (bus->device->address)
            {
              result = bus->device->address(bus->context, msg->addr, read);
              if (result < 0) break;
            }
        }
      if (read)
        {
          /* Latch device read data after address acknowledgement, before
           * clocking its payload. A conversion during a burst must not
           * silently splice bytes from different measurement epochs. */
          result = bus->device->message(bus->context, msg, start, repeated, stop);
          if (result >= 0 && bus->device->read_byte)
            {
              for (int byte = 0; byte < msg->length; byte++)
                {
                  bench_wire_delay(9, msg->frequency);
                  result = bus->device->read_byte(bus->context, msg, byte, &msg->buffer[byte]);
                  if (result < 0) break;
                }
            }
          else if (result >= 0) bench_wire_delay(9 * msg->length, msg->frequency);
        }
      else
        {
          if (bus->device->write_ack)
            {
              for (int byte = 0; byte < msg->length; byte++)
                {
                  bench_wire_delay(9, msg->frequency);
                  result = bus->device->write_ack(bus->context, msg->buffer[byte], byte);
                  if (result < 0) break;
                }
              if (result < 0) break;
            }
          else bench_wire_delay(9 * msg->length, msg->frequency);
          result = bus->device->message(bus->context, msg, start, repeated, stop);
        }
      if (result < 0)
        {
          break;
        }
      if (stop)
        {
          bus->active = false;
        }
    }
out:
  if (result < 0)
    {
      bus->active = false;
      if (bus->device->transfer_failed)
        bus->device->transfer_failed(bus->context, msgs, count, result);
    }
  nxmutex_unlock(&bus->mutex);
  return result < 0 ? result : 0;
}

static int i2c_setup(struct i2c_master_s *dev)
{
  return 0;
}

static int i2c_shutdown(struct i2c_master_s *dev)
{
  struct bench_i2c *bus = (struct bench_i2c *)dev;
  assert(!bus->active);
  return 0;
}

static const struct i2c_ops_s i2c_ops =
{
  .transfer = i2c_transfer, .setup = i2c_setup, .shutdown = i2c_shutdown
};

void bench_i2c_init(struct bench_i2c *bus, const struct bench_i2c_device *device,
                    void *context, uint32_t max_frequency)
{
  memset(bus, 0, sizeof(*bus));
  bus->dev.ops = &i2c_ops;
  nxmutex_init(&bus->mutex);
  bus->device = device;
  bus->context = context;
  bus->max_frequency = max_frequency;
}
