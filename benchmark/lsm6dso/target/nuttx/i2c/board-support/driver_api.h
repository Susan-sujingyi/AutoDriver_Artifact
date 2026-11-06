/* Public native ABI/product configuration only; no register recipe. */
#ifndef LSM6DSO_H
#define LSM6DSO_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* LSM6DSO acceleration only, I2C0/0x6a/100kHz, sensor-native XYZ, +/-2g,
 * 104Hz high-performance, LPF2/high-pass off; gyro, FIFO and sensorhub off.
 * Register sensor_accel<devno> via New Sensor. Event x/y/z are signed m/s^2;
 * unused temperature is NAN, timestamp monotonic microseconds.
 * Factory validates/configures before publishing the node, within100ms in the
 * digital fixture; failure publishes nothing and a subsequent call may retry.
 * Supply/initial boot are complete; sensors start powered down at reset
 * settings, with register bank possibly left selected by a prior probe.
 * Enabled fetch returns exactly one settled, coherent, previously undelivered
 * XYZ conversion epoch or negative errno within100ms. Equal values in new
 * epochs are valid. Whole-XYZ coherence is an explicit benchmark requirement.
 * Disabled/invalid/error fetch leaves the caller buffer unchanged. Restore
 * resources on all terminal paths. Concurrent requests may return busy.
 * No gyro/temperature output, dynamic configuration or other optional ops.
 */
int lsm6dso_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
