/* Public native New Sensor ABI and product configuration; no device protocol. */
#ifndef DHT20_H
#define DHT20_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* Exact DHT20 at 0x38, I2C0 100 kHz, CRC enabled, single-shot temperature/RH.
 * Register sensor_ambient_temp<devno> and sensor_humi<devno> fetch nodes.
 * Native sensor_temp.temperature is degC; sensor_humi.humidity is percent RH.
 * Timestamp: monotonic microseconds at completed valid acquisition.
 * Each channel consumes a measurement at most once; both channels may share
 * one valid acquisition. Known acquisition errors invalidate cached samples.
 * Bound startup to 600 ms and enabled fetch to 250 ms. Disabled/invalid-buffer
 * fetches and failed measurements return negative errno, without a sample.
 * Concurrent requests may return busy. Release resources on terminal returns.
 * Startup performs calibration checks without acquiring a measurement;
 * failure leaves no registered nodes. No optional controls are required.
 */
int dht20_register(struct i2c_master_s *i2c, uint8_t address, int devno);
#endif
