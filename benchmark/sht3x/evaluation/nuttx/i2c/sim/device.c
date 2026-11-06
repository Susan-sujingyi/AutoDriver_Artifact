/* SHT3x-DIS v7: single shot high repeatability without clock stretching,
 * 15.5 ms maximum SHT30 conversion, command/reset/status and CRC framing.
 * Real NuttX owns registration, activation, locks, waits and timestamps.
 */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <nuttx/sensors/sensor.h>
#include <nuttx/sensors/sht3x.h>
#include "bus.h"
#include "observer.h"
#include "recovery_observer.h"
#include "outcome.h"
#include "scenario.h"

static struct bench_i2c bus;
static const char *paths[] = {"/dev/uorb/sensor_ambient_temp0", "/dev/uorb/sensor_humi0"};
static const int types[] = {SENSOR_TYPE_AMBIENT_TEMPERATURE, SENSOR_TYPE_RELATIVE_HUMIDITY};
static uint64_t power_ready, ready_at;
static uint8_t tx[2], frame[6];
static unsigned txpos, rxpos, current, commands, acquired[2], epoch;
static bool conversion, status_read;
static uint16_t status_word = 0x0010; /* Reset detected, heater disabled. */
static int corrupt_byte = -1;

static uint8_t crc_word(uint16_t word)
{
  /* Long division, rather than the candidate's conventional CRC loop. */
  unsigned p = (0xffu << 16) ^ ((unsigned)word << 8);
  for (int shift = 15; shift >= 0; shift--)
    if (p & (1u << (shift + 8))) p ^= 0x131u << shift;
  return p;
}

static int command(uint16_t value, uint64_t now)
{
  if (now < power_ready) return -ENXIO;
  status_read = false;
  switch (value)
    {
      case 0x30a2:
        conversion = false;
        power_ready = now + 1500;
        status_word = 0x0010;
        break;
      case 0x3066: status_word &= ~0x2000; break; /* Optional heater disable. */
      case 0x3041: status_word &= ~0x8013; break;
      case 0x3093: conversion = false; break;
      case 0xf32d: status_read = true; break;
      case 0x2400:
        if (conversion && now < ready_at)
          bench_model_unsupported("sht3x_overlapping_conversion");
        conversion = true;
        ready_at = now + 15500;
        commands++;
        memcpy(frame, vectors[current].bytes, sizeof(frame));
        break;
      default:
        assert(!"SHT30 task requires high repeatability without clock stretching");
    }
  return 0;
}

static int message(void *context, struct i2c_msg_s *msg,
                   bool start, bool repeated, bool stop)
{
  assert(msg->addr == BENCH_I2C_ADDRESS && !(msg->flags & I2C_M_TEN));
  if (!(msg->flags & I2C_M_READ))
    {
      if (start) txpos = 0;
      assert(txpos + msg->length <= 2);
      memcpy(tx + txpos, msg->buffer, msg->length);
      txpos += msg->length;
      if (txpos == 2) return command((tx[0] << 8) | tx[1], bench_now_us());
      assert(!stop);
      return 0;
    }
  if (bench_now_us() < power_ready) return -ENXIO;
  if (start) rxpos = 0;
  if (status_read)
    {
      if (rxpos + msg->length > 3) bench_model_unsupported("sht3x_status_extra_bytes");
      uint8_t data[] = {status_word >> 8, status_word & 255, crc_word(status_word)};
      memcpy(msg->buffer, data + rxpos, msg->length);
      rxpos += msg->length;
      return 0;
    }
  if (!conversion || bench_now_us() < ready_at) return -ENXIO;
  if (rxpos + msg->length > 6) bench_model_unsupported("sht3x_data_extra_bytes");
  for (unsigned i = 0; i < msg->length; i++, rxpos++)
    msg->buffer[i] = frame[rxpos] ^ ((int)rxpos == corrupt_byte ? 1 : 0);
  if (rxpos >= 3) acquired[0] = commands;
  if (rxpos >= 6) acquired[1] = commands;
  return 0;
}

static int acknowledge_address(void *context, uint16_t address, bool reading)
{
  assert(address == BENCH_I2C_ADDRESS);
  if (bench_now_us() < power_ready) return -ENXIO;
  if (reading && !status_read && (!conversion || bench_now_us() < ready_at)) return -ENXIO;
  return 0;
}

static const struct bench_i2c_device device = {.message = message, .address = acknowledge_address};

static void setup(int *fds)
{
  bench_i2c_init(&bus, &device, NULL, 100000);
  power_ready = bench_now_us() + 1500;
  assert(sht3x_register(&bus.dev, BENCH_I2C_ADDRESS, 0) == 0);
  assert(commands == 0); /* Factory performs no measurement. */
  for (unsigned i = 0; i < 2; i++)
    { fds[i] = open(paths[i], O_RDONLY); assert(fds[i] >= 0); }
}

static void sample_observed(int fd, unsigned channel, unsigned index, bool permit_failure)
{
  union {struct sensor_temp t; struct sensor_humi h;} event;
  size_t size = channel ? sizeof(event.h) : sizeof(event.t);
  current = index;
  unsigned before = acquired[channel], previous_commands = commands;
  uint64_t start = bench_now_us();
  memset(&event, 0xa5, sizeof(event));
  memset(&event, 0xa5, sizeof(event));
  ssize_t observed_result = bench_observe_read(fd, &event, size, sizeof(event), permit_failure);
  if (observed_result < 0)
    {
      assert(bench_now_us() - start <= 100000);
      assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
      return;
    }

  assert(observed_result == size);
  assert(bench_now_us() - start <= 100000);
  assert(commands > previous_commands && acquired[channel] > before);
  double value = channel ? event.h.humidity : event.t.temperature;
  uint64_t stamp = channel ? event.h.timestamp : event.t.timestamp;
  assert(isfinite(value) && value >= vectors[index].low[channel] - 0.00001 &&
         value <= vectors[index].high[channel] + 0.00001);
  assert(stamp >= ready_at && stamp <= bench_now_us());
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
  printf("BENCHSIM {\"event\":\"sample\",\"channel\":%u,\"epoch\":%u,\"value\":%.8f}\n",
         channel, acquired[channel], value);
}
static void sample(int fd, unsigned channel, unsigned index)
{
  sample_observed(fd, channel, index, false);
}

static void failure(int fd, unsigned channel)
{
  union {struct sensor_temp t; struct sensor_humi h;} event, before;
  size_t size = channel ? sizeof(event.h) : sizeof(event.t);
  memset(&event, 0xa5, sizeof(event));
  memcpy(&before, &event, sizeof(event));
  uint64_t start = bench_now_us();
  assert(read(fd, &event, size) < 0);
  assert(!memcmp(&event, &before, sizeof(event)) && bench_now_us() - start <= 100000);
  assert(!bus.active && !nxmutex_is_locked(&bus.mutex));
}

int bench_case_main(int argc, char **argv)
{
  if (argc != 2) return 64;
  if (!strcmp(argv[1], "model/conversion"))
    {
      assert(crc_word(0xbeef) == 0x92);
      power_ready = 1500;
      assert(command(0x2400, 1499) == -ENXIO && commands == 0);
      assert(command(0x2400, 1500) == 0 && ready_at == 17000 && commands == 1);
      assert(!memcmp(frame, vectors[0].bytes, 6));
      assert(command(0x30a2, 17000) == 0 && !conversion && power_ready == 18500);
      return 0;
    }
  int fds[2];
  setup(fds);
  for (unsigned i = 0; i < BENCH_VECTOR_COUNT; i++)
    if (!strcmp(argv[1], vectors[i].id))
      { sample(fds[0], 0, i); sample(fds[1], 1, i); goto done; }
  if (!strcmp(argv[1], "data/stable_change"))
    {
      for (unsigned i = 0; i < 2; i++)
        { sample(fds[0], 0, i); sample(fds[1], 1, i); }
    }
  else if (!strcmp(argv[1], "os/lifecycle"))
    {
      for (unsigned i = 0; i < 2; i++)
        {
          char small[1];
          assert(read(fds[i], small, sizeof(small)) < 0);
          assert(close(fds[i]) == 0);
          unsigned messages = bus.messages;
          size_t size = i ? sizeof(struct sensor_humi) : sizeof(struct sensor_temp);
          bench_sensor_check_disabled(types[i], 0, size);
          assert(bus.messages == messages);
          sample(fds[1-i], 1-i, 1);
          fds[i] = open(paths[i], O_RDONLY);
          assert(fds[i] >= 0);
          sample(fds[i], i, 0);
        }
    }
  else if (!strncmp(argv[1], "integrity/crc_byte_", 19))
    {
      corrupt_byte = atoi(argv[1] + 19);
      assert(corrupt_byte >= 0 && corrupt_byte < 6);
      failure(fds[corrupt_byte / 3], corrupt_byte / 3);
    }
  else if (!strcmp(argv[1], "integrity/transport_error"))
    {
      for (unsigned i = 0; i < 2; i++)
        { bus.next_transfer_error = -EIO; sample_observed(fds[i],i,0,true); bench_finish_transient(&bus.next_transfer_error); }
    }
  else return 65;
done:
  assert(close(fds[0]) == 0 && close(fds[1]) == 0);
  return 0;
}
