/* Virtual peripherals implement the frozen OS vtables, not replacement APIs. */
#ifndef BENCH_SIM_BUS_H
#define BENCH_SIM_BUS_H
#include <nuttx/i2c/i2c_master.h>
#include <nuttx/spi/spi.h>
#include <nuttx/mutex.h>

struct bench_spi;
struct bench_spi_device
{
  void (*select)(void *context, bool selected);
  uint8_t (*exchange)(void *context, uint8_t value);
};

struct bench_spi
{
  struct spi_dev_s dev;
  mutex_t mutex;
  const struct bench_spi_device *device;
  void *context;
  uint32_t device_id, frequency, max_frequency;
  enum spi_mode_e mode, required_mode;
  int bits;
  bool selected;
  int next_lock_error;
  unsigned words, selections;
};

struct bench_i2c_device
{
  /* A message can start after STOP, repeat START, or continue without START.
   * The device model sees those distinctions even across transfer calls.
   */
  int (*message)(void *context, struct i2c_msg_s *msg,
                 bool start, bool repeated, bool stop);
  void (*reset)(void *context);
  /* Optional ACK-stage probes. Address runs after its nine wire clocks and
   * before any payload. A negative write ACK stops at that byte; message()
   * receives only fully transferred write messages. Device models that need
   * partial-write effects can implement them in write_ack(). Read message()
   * latches a coherent result; an error clocks no payload bytes.
   */
  int (*address)(void *context, uint16_t address, bool reading);
  int (*write_ack)(void *context, uint8_t value, unsigned index);
  /* Optional byte-clocked read path for devices with pair BDU or streaming
   * registers. message() validates/prepares the read; read_byte() supplies
   * each byte after its nine wire clocks. A negative return terminates at
   * that byte. Without this hook, message() latches the entire read as above.
   */
  int (*read_byte)(void *context, const struct i2c_msg_s *msg,
                   unsigned index, uint8_t *value);
  /* Host evidence invalidation after an actual failed transfer, not merely
   * after arming a future fault. Does not change the sensor's registers. */
  void (*transfer_failed)(void *context, const struct i2c_msg_s *messages,
                          int count, int error);
};

struct bench_i2c
{
  struct i2c_master_s dev;
  mutex_t mutex;
  const struct bench_i2c_device *device;
  void *context;
  uint32_t max_frequency;
  bool active, reading;
  uint16_t address;
  int next_transfer_error;
  unsigned messages;
};

void bench_spi_init(struct bench_spi *bus, const struct bench_spi_device *device,
                    void *context, uint32_t id, enum spi_mode_e mode,
                    uint32_t max_frequency);
void bench_i2c_init(struct bench_i2c *bus, const struct bench_i2c_device *device,
                    void *context, uint32_t max_frequency);
uint64_t bench_now_us(void);
void bench_wire_delay(unsigned bits, uint32_t frequency);
#endif
