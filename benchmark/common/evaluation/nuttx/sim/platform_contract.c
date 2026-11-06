/* Exercises real OS operations; no substitutes for sleep, locks or Sensor. */
#include <nuttx/config.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <semaphore.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/mutex.h>
#include <nuttx/arch.h>
#include <nuttx/signal.h>
#include <nuttx/sched.h>
#include <nuttx/wdog.h>
#include <nuttx/irq.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/ioctl.h>
#include "bus.h"
#include "observer.h"
#include "outcome.h"

static unsigned spi_edges, spi_bytes, i2c_messages;
static int address_error, payload_error_at = -1;
static unsigned acknowledged, complete_messages;
static unsigned error_notifications;
static int notified_error;
static void transfer_failed(void *context, const struct i2c_msg_s *messages,
                            int count, int error)
{
  assert(messages == context && count == 1 && error < 0);
  error_notifications++;
  notified_error = error;
}

static int ack_address(void *context, uint16_t address, bool reading)
{
  assert(address == 0x42);
  return address_error;
}

static int ack_byte(void *context, uint8_t value, unsigned index)
{
  acknowledged++;
  return (int)index == payload_error_at ? -EIO : 0;
}

static int ack_message(void *context, struct i2c_msg_s *msg,
                       bool start, bool repeated, bool stop)
{
  complete_messages++;
  return msg->flags & I2C_M_READ ? -ENXIO : 0;
}

static void nack_contract(void)
{
  const struct bench_i2c_device device = {.message = ack_message,
    .address = ack_address, .write_ack = ack_byte, .transfer_failed=transfer_failed};
  struct bench_i2c bus;
  uint8_t bytes[4] = {1, 2, 3, 4};
  struct i2c_msg_s msg = {.frequency = 100000, .addr = 0x42,
    .buffer = bytes, .length = sizeof(bytes)};
  bench_i2c_init(&bus, &device, &msg, 100000);
  bus.next_transfer_error = -EIO;
  assert(error_notifications == 0); /* Arming is not an observed error. */
  assert(I2C_TRANSFER(&bus.dev, &msg, 1) == -EIO);
  assert(error_notifications == 1 && notified_error == -EIO);
  uint64_t start = bench_now_us();
  address_error = -ENXIO;
  assert(I2C_TRANSFER(&bus.dev, &msg, 1) == -ENXIO);
  assert(bench_now_us() == start + 90 && acknowledged == 0 && complete_messages == 0);
  assert(error_notifications == 2 && notified_error == -ENXIO);
  address_error = 0;
  payload_error_at = 1;
  start = bench_now_us();
  assert(I2C_TRANSFER(&bus.dev, &msg, 1) == -EIO);
  assert(bench_now_us() == start + 270 && acknowledged == 2 && complete_messages == 0);
  assert(error_notifications == 3 && notified_error == -EIO);
  payload_error_at = -1;
  start = bench_now_us();
  assert(I2C_TRANSFER(&bus.dev, &msg, 1) == 0);
  assert(bench_now_us() == start + 450 && acknowledged == 6 && complete_messages == 1);
  assert(error_notifications == 3);
  msg.flags = I2C_M_READ;
  start = bench_now_us();
  assert(I2C_TRANSFER(&bus.dev, &msg, 1) == -ENXIO);
  assert(bench_now_us() == start + 90 && complete_messages == 2);
  assert(error_notifications == 4 && notified_error == -ENXIO);
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  puts("BENCHSIM {\"event\":\"nack_timing_contract\",\"address_us\":90,\"byte1_us\":270}");
}

static uint64_t read_clock_start;
static int read_fault_byte = -1;
static unsigned byte_read_calls;
static int read_begin(void *context, struct i2c_msg_s *msg,
                      bool start, bool repeated, bool stop)
{
  assert(start && !repeated && stop && msg->flags == I2C_M_READ);
  assert(bench_now_us() == read_clock_start + 90);
  return 0;
}
static int read_clocked(void *context, const struct i2c_msg_s *msg,
                        unsigned index, uint8_t *value)
{
  assert(bench_now_us() == read_clock_start + 90 * (index + 2));
  byte_read_calls++;
  if ((int)index == read_fault_byte) return -EIO;
  *value = 0x30 + index;
  return 0;
}
static void read_byte_contract(void)
{
  const struct bench_i2c_device device = {.message=read_begin,.read_byte=read_clocked,.transfer_failed=transfer_failed};
  struct bench_i2c bus;
  uint8_t bytes[4]={0};
  struct i2c_msg_s msg={.frequency=100000,.addr=0x42,.flags=I2C_M_READ,.buffer=bytes,.length=4};
  error_notifications=0;
  bench_i2c_init(&bus,&device,&msg,100000);
  read_clock_start=bench_now_us();
  assert(I2C_TRANSFER(&bus.dev,&msg,1)==0);
  assert(byte_read_calls==4 && !memcmp(bytes,"0123",4));
  assert(bench_now_us()==read_clock_start+450);
  assert(error_notifications==0);
  read_fault_byte=1;byte_read_calls=0;memset(bytes,0xa5,4);
  read_clock_start=bench_now_us();
  assert(I2C_TRANSFER(&bus.dev,&msg,1)==-EIO);
  assert(bench_now_us()==read_clock_start+270 && byte_read_calls==2);
  assert(bytes[0]==0x30 && bytes[1]==0xa5 && bytes[2]==0xa5);
  assert(error_notifications==1 && notified_error==-EIO);
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  puts("BENCHSIM {\"event\":\"byte_clocked_read_contract\",\"byte_us\":90,\"partial_error_us\":270}");
}
static void spi_edge(void *context, bool selected)
{
  spi_edges++;
}

static uint8_t spi_byte(void *context, uint8_t value)
{
  spi_bytes++;
  return value ^ 0xa5;
}

static int i2c_message(void *context, struct i2c_msg_s *msg,
                        bool start, bool repeated, bool stop)
{
  /* First pair: repeated START. Second pair: continuation. */
  static const bool starts[] = {true, true, true, false};
  static const bool repeats[] = {false, true, false, false};
  static const bool stops[] = {false, true, false, true};
  assert(i2c_messages < 4);
  assert(start == starts[i2c_messages]);
  assert(repeated == repeats[i2c_messages]);
  assert(stop == stops[i2c_messages]);
  if (msg->flags & I2C_M_READ)
    {
      memset(msg->buffer, 0x42, msg->length);
    }
  i2c_messages++;
  return 0;
}

static void bus_contract(void)
{
  static const struct bench_spi_device spidev = {spi_edge, spi_byte};
  static const struct bench_i2c_device i2cdev = {.message = i2c_message};
  struct bench_spi spi;
  struct bench_i2c i2c;
  uint8_t tx[] = {0x10, 0x20}, rx[2], value = 0;
  struct i2c_msg_s msgs[2] =
    {{.frequency = 100000, .addr = 0x42, .flags = I2C_M_NOSTOP,
       .buffer = tx, .length = 1},
     {.frequency = 100000, .addr = 0x42, .flags = I2C_M_READ,
       .buffer = &value, .length = 1}};
  uint64_t before;

  bench_spi_init(&spi, &spidev, NULL, SPIDEV_TEMPERATURE(0), SPIDEV_MODE3, 1000000);
  spi.next_lock_error = -EIO;
  assert(SPI_LOCK(&spi.dev, true) == -EIO);
  assert(SPI_LOCK(&spi.dev, true) == 0);
  SPI_SETMODE(&spi.dev, SPIDEV_MODE3);
  SPI_SETBITS(&spi.dev, 8);
  SPI_SETFREQUENCY(&spi.dev, 1000000);
  SPI_SELECT(&spi.dev, SPIDEV_TEMPERATURE(0), true);
  before = bench_now_us();
  SPI_EXCHANGE(&spi.dev, tx, rx, 2);
  assert(rx[0] == (tx[0] ^ 0xa5) && rx[1] == (tx[1] ^ 0xa5));
  assert(SPI_SEND(&spi.dev, 0x10) == rx[0]);
  SPI_RECVBLOCK(&spi.dev, &value, 1);
  assert(value == (0xff ^ 0xa5));
  assert(bench_now_us() == before + 32);
  assert(sensor_get_timestamp() == bench_now_us());
  SPI_SELECT(&spi.dev, SPIDEV_TEMPERATURE(0), false);
  assert(SPI_LOCK(&spi.dev, false) == 0);
  assert(spi_edges == 2 && spi_bytes == 4 && spi.selections == 1);

  bench_i2c_init(&i2c, &i2cdev, NULL, 400000);
  i2c.next_transfer_error = -ENXIO;
  assert(I2C_TRANSFER(&i2c.dev, msgs, 2) == -ENXIO);
  assert(i2c_messages == 0);
  assert(I2C_TRANSFER(&i2c.dev, msgs, 2) == 0 && value == 0x42);
  msgs[0].flags = 0;
  msgs[1].flags = I2C_M_NOSTART;
  assert(I2C_TRANSFER(&i2c.dev, msgs, 2) == 0);
  assert(i2c_messages == 4 && !i2c.active);
  puts("BENCHSIM {\"event\":\"bus_contract\",\"spi_words\":4,\"i2c_messages\":4}");
}

static mutex_t lock = NXMUTEX_INITIALIZER;
static sem_t entered;
static unsigned phase;
static uint64_t worker_time;
static unsigned timer_calls;
static uint64_t timer_time, busy_worker_time;
static bool worker_woke;
static sem_t busy_entered;

static void timer_callback(wdparm_t arg)
{
  assert(up_interrupt_context());
  timer_calls++;
  timer_time = bench_now_us();
}

static void *busy_worker(void *arg)
{
  assert(sem_post(&busy_entered) == 0);
  assert(nxsig_usleep(5000) == 0);
  busy_worker_time = bench_now_us();
  worker_woke = true;
  return NULL;
}

static void interrupt_contract(void)
{
  struct wdog_s watchdog = {0};
  uint64_t begin = bench_now_us();
  assert(wd_start(&watchdog, 5, timer_callback, 0) == 0);
  up_udelay(15000);
  assert(timer_calls == 1 && timer_time >= begin + 5000 && timer_time <= begin + 7000);
  timer_calls = 0;
  irqstate_t flags = enter_critical_section();
  assert(wd_start(&watchdog, 5, timer_callback, 0) == 0);
  begin = bench_now_us();
  up_udelay(15000);
  assert(timer_calls == 0);
  leave_critical_section(flags);
  assert(timer_calls == 1 && timer_time == begin + 15000);
  timer_calls = 0;
  assert(wd_start(&watchdog, 2, timer_callback, 0) == 0);
  assert(wd_cancel(&watchdog) == 0);
  up_udelay(10000);
  assert(timer_calls == 0);

  pthread_t thread;
  pthread_attr_t attr;
  struct sched_param priority = {.sched_priority = 110};
  assert(sem_init(&busy_entered, 0, 0) == 0);
  assert(pthread_attr_init(&attr) == 0);
  assert(pthread_attr_setinheritsched(&attr, PTHREAD_EXPLICIT_SCHED) == 0);
  assert(pthread_attr_setschedparam(&attr, &priority) == 0);
  assert(pthread_create(&thread, &attr, busy_worker, NULL) == 0);
  assert(sem_wait(&busy_entered) == 0);
  begin = bench_now_us();
  up_udelay(15000);
  assert(worker_woke && busy_worker_time >= begin + 5000 && busy_worker_time <= begin + 7000);
  assert(pthread_join(thread, NULL) == 0);
  assert(pthread_attr_destroy(&attr) == 0);
  assert(sem_destroy(&busy_entered) == 0);
  puts("BENCHSIM {\"event\":\"interrupt_contract\",\"critical_mask\":true,\"cancel\":true,\"busy_preemption\":true}");
}

static void *worker(void *arg)
{
  assert(sem_post(&entered) == 0);
  assert(nxmutex_lock(&lock) == 0);
  assert(phase == 1);
  phase = 2;
  worker_time = sensor_get_timestamp();
  assert(nxmutex_unlock(&lock) == 0);
  return NULL;
}

static bool active;
static unsigned activations, fetches;
static bool concurrent_fetch;
static unsigned fetch_entries, fetch_inflight, max_fetch_inflight;
static sem_t fetch_entered, fetch_release, reader_started;
static int activate(struct sensor_lowerhalf_s *lower, struct file *filep,
                    bool enable)
{
  active = enable;
  activations++;
  return 0;
}

static int fetch(struct sensor_lowerhalf_s *lower, struct file *filep,
                 char *buffer, size_t length)
{
  struct sensor_temp value;
  if (!active || length < sizeof(value))
    {
      return -EINVAL;
    }

  if (concurrent_fetch)
    {
      fetch_entries++;
      fetch_inflight++;
      if (fetch_inflight > max_fetch_inflight) max_fetch_inflight = fetch_inflight;
      assert(sem_post(&fetch_entered) == 0);
      assert(sem_wait(&fetch_release) == 0);
      fetch_inflight--;
    }

  value.timestamp = sensor_get_timestamp();
  value.temperature = 23.5f;
  memcpy(buffer, &value, sizeof(value));
  fetches++;
  return sizeof(value);
}

static const struct sensor_ops_s ops = {.activate = activate, .fetch = fetch};
static struct sensor_lowerhalf_s sensor =
  {.ops = &ops, .type = SENSOR_TYPE_AMBIENT_TEMPERATURE};

static void *sensor_reader(void *arg)
{
  struct sensor_temp event;
  int fd = *(int *)arg;
  assert(sem_post(&reader_started) == 0);
  assert(read(fd, &event, sizeof(event)) == sizeof(event));
  assert(event.temperature == 23.5f);
  return NULL;
}

static void concurrent_sensor_contract(void)
{
  /* Two real opens share the real Sensor upper-half lock and subscriber
   * count. A delayed fetch must serialize another reader without disabling
   * the sensor when only one of the subscribers closes. */
  pthread_t first, second;
  assert(sensor_register(&sensor, 0) == 0);
  unsigned before = activations;
  int fd1 = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
  int fd2 = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
  assert(fd1 >= 0 && fd2 >= 0 && active && activations == before + 1);
  assert(sem_init(&fetch_entered, 0, 0) == 0);
  assert(sem_init(&fetch_release, 0, 0) == 0);
  assert(sem_init(&reader_started, 0, 0) == 0);
  concurrent_fetch = true;
  assert(pthread_create(&first, NULL, sensor_reader, &fd1) == 0);
  assert(sem_wait(&reader_started) == 0);
  assert(sem_wait(&fetch_entered) == 0);
  assert(pthread_create(&second, NULL, sensor_reader, &fd2) == 0);
  assert(sem_wait(&reader_started) == 0);
  assert(nxsig_usleep(3000) == 0);
  assert(fetch_entries == 1 && fetch_inflight == 1);
  assert(sem_post(&fetch_release) == 0);
  assert(sem_wait(&fetch_entered) == 0);
  assert(fetch_entries == 2 && max_fetch_inflight == 1);
  assert(sem_post(&fetch_release) == 0);
  assert(pthread_join(first, NULL) == 0 && pthread_join(second, NULL) == 0);
  concurrent_fetch = false;
  assert(close(fd1) == 0 && active && activations == before + 1);
  assert(close(fd2) == 0 && !active && activations == before + 2);
  assert(sem_destroy(&fetch_entered) == 0 && sem_destroy(&fetch_release) == 0);
  assert(sem_destroy(&reader_started) == 0);
  sensor_unregister(&sensor, 0);
  puts("BENCHSIM {\"event\":\"concurrent_sensor_contract\",\"readers\":2,\"max_fetch_inflight\":1,\"last_close_deactivates\":true}");
}

static sem_t cancellation_entered;
static bool cancelled_worker_returned;
static void *cancellable_worker(void *arg)
{
  /* Default frozen configuration has no deferred POSIX cancellation points.
   * Request asynchronous cancellation explicitly; do not substitute fake
   * scheduler behavior or hold a driver mutex across this probe. */
  assert(pthread_setcanceltype(PTHREAD_CANCEL_ASYNCHRONOUS, NULL) == 0);
  assert(sem_post(&cancellation_entered) == 0);
  nxsig_usleep(100000);
  cancelled_worker_returned = true;
  return NULL;
}

static void cancellation_contract(void)
{
  pthread_t thread;
  void *result = NULL;
  assert(sem_init(&cancellation_entered, 0, 0) == 0);
  assert(pthread_create(&thread, NULL, cancellable_worker, NULL) == 0);
  assert(sem_wait(&cancellation_entered) == 0);
  assert(nxsig_usleep(1000) == 0);
  assert(!cancelled_worker_returned);
  assert(pthread_cancel(thread) == 0);
  assert(pthread_join(thread, &result) == 0 && result == PTHREAD_CANCELED);
  assert(nxsig_usleep(150000) == 0 && !cancelled_worker_returned);
  assert(sem_destroy(&cancellation_entered) == 0);
  puts("BENCHSIM {\"event\":\"cancellation_contract\",\"mode\":\"explicit_async\",\"sleep_cancelled\":true,\"ghost_wakeup\":false}");
}

int bench_case_main(int argc, char **argv)
{
  uint64_t begin, after_signal, after_scheduler;
  pthread_t thread;
  struct sensor_temp event;
  int fd;

  if (argc == 2 && !strcmp(argv[1], "--assert-contract"))
    {
      assert(!"BENCH_EXPECTED_ASSERTION");
    }
  if (argc == 2 && !strcmp(argv[1], "--unsupported-contract"))
    {
      bench_model_unsupported("contract_probe");
    }

  begin = sensor_get_timestamp();
  assert(nxsig_usleep(20000) == 0);
  after_signal = sensor_get_timestamp();
  assert(after_signal >= begin + 20000);
  assert(after_signal <= begin + 22000);
  nxsched_usleep(17000);
  after_scheduler = sensor_get_timestamp();
  assert(after_scheduler >= after_signal + 17000);
  assert(after_scheduler <= after_signal + 19000);

  uint64_t before_busy = bench_now_us();
  up_udelay(37);
  up_mdelay(2);
  up_ndelay(1500);
  assert(bench_now_us() == before_busy + 2038);
  assert(sensor_get_timestamp() == bench_now_us());

  assert(sem_init(&entered, 0, 0) == 0);
  assert(nxmutex_lock(&lock) == 0);
  assert(pthread_create(&thread, NULL, worker, NULL) == 0);
  assert(sem_wait(&entered) == 0);
  assert(nxsig_usleep(11000) == 0);
  assert(phase == 0); /* The worker really blocked on the real OS mutex. */
  phase = 1;
  assert(nxmutex_unlock(&lock) == 0);
  assert(pthread_join(thread, NULL) == 0);
  assert(phase == 2 && worker_time >= after_scheduler + 11000);
  assert(sem_destroy(&entered) == 0);

  assert(sensor_register(&sensor, 0) == 0);
  assert(bench_sensor_find(SENSOR_TYPE_AMBIENT_TEMPERATURE, 0) == &sensor);
  fd = open("/dev/uorb/sensor_ambient_temp0", O_RDONLY);
  assert(fd >= 0);
  assert(active && activations == 1);
  assert(read(fd, &event, sizeof(event)) == sizeof(event));
  assert(event.temperature == 23.5f && fetches == 1 && active);
  assert(event.timestamp >= worker_time);
  assert(close(fd) == 0);
  assert(!active && activations == 2);
  bench_sensor_check_disabled(SENSOR_TYPE_AMBIENT_TEMPERATURE, 0, sizeof(event));
  sensor_unregister(&sensor, 0);
  assert(bench_sensor_find(SENSOR_TYPE_AMBIENT_TEMPERATURE, 0) == NULL);
  assert(open("/dev/uorb/sensor_ambient_temp0", O_RDONLY) < 0);

  concurrent_sensor_contract();
  cancellation_contract();

  sensor.type=SENSOR_TYPE_CUSTOM;
  assert(sensor_custom_register(&sensor,"/dev/uorb/contract_custom0",sizeof(event))==0);
  assert(bench_sensor_find_custom("/dev/uorb/contract_custom0")==&sensor);
  fd=open("/dev/uorb/contract_custom0",O_RDONLY);assert(fd>=0 && active);
  assert(read(fd,&event,sizeof(event))==sizeof(event) && event.temperature==23.5f);
  assert(close(fd)==0 && !active);
  bench_sensor_check_lower_disabled(bench_sensor_find_custom("/dev/uorb/contract_custom0"),sizeof(event));
  sensor_custom_unregister(&sensor,"/dev/uorb/contract_custom0");
  assert(bench_sensor_find_custom("/dev/uorb/contract_custom0")==NULL);
  assert(open("/dev/uorb/contract_custom0",O_RDONLY)<0);

  bus_contract();
  nack_contract();
  read_byte_contract();
  interrupt_contract();

  printf("BENCHSIM {\"event\":\"platform_contract\","
         "\"signal_sleep_us\":%llu,\"scheduler_sleep_us\":%llu,"
         "\"worker_time_us\":%llu,\"sensor_fetches\":%u}\n",
         (unsigned long long)(after_signal - begin),
         (unsigned long long)(after_scheduler - after_signal),
         (unsigned long long)worker_time, fetches);
  return 0;
}
