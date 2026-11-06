/* Public New Sensor ABI and product configuration; no register recipe. */
#ifndef LPS22HB_H
#define LPS22HB_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* Exact LPS22HB, I2C0, 0x5c, 100 kHz. Continuous 1 Hz, BDU enabled,
 * low-pass filter disabled, FIFO disabled, absolute pressure.
 * Register sensor_ambient_temp<devno> and sensor_baro<devno> atomically.
 * Native sensor_temp.temperature uses degC; sensor_baro.pressure uses hPa;
 * its unused temperature field is NAN. Timestamps are monotonic microseconds.
 * Factory initializes/validates before publishing nodes, within 100 ms in the
 * digital fixture. Failure leaves no nodes and is retryable.
 * Enabled fetch returns exactly one native event or negative errno, within
 * 2200 ms in the digital fixture. Disabled/invalid/error requests publish no
 * sample. Each channel consumes coherent ready measurements; subsequent
 * successes on the same channel consume newer conversions. An unread ready
 * sample is eligible. Release resources on every terminal path and permit
 * recovery after transient errors. Concurrent requests may return busy.
 * No optional controls or additional channels are required.
 */
int lps22hb_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
