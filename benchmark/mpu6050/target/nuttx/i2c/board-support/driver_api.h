/* Public native ABI/product configuration only; no sensor register recipe. */
#ifndef MPU6050_H
#define MPU6050_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* MPU6050 acceleration only, I2C0/0x68/100kHz, sensor-native XYZ, +/-2g.
 * Divider9, DLPF0, FSYNC/FIFO/auxiliary-master disabled, gyro range250dps.
 * Register sensor_accel<devno> via New Sensor. Event x/y/z are signed m/s^2;
 * unused temperature is NAN, timestamp monotonic microseconds.
 * Factory validates/configures before publishing the node, within100ms in the
 * digital fixture; failure publishes nothing and a subsequent call may retry.
 * Enabled fetch returns exactly one coherent, previously undelivered XYZ epoch
 * or negative errno within100ms. Equal values in successive epochs are valid.
 * Disabled/invalid/error fetch leaves the caller buffer unchanged. Restore
 * resources on all terminal paths. Concurrent requests may return busy.
 * No gyro/temperature output, dynamic configuration or other optional ops.
 */
int mpu6050_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
