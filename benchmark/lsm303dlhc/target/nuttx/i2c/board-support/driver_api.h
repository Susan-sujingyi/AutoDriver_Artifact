/* Public New Sensor ABI and product policy, without a register recipe. */
#ifndef LSM303DLHC_H
#define LSM303DLHC_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* LSM303DLHC magnetic XYZ only, I2C0/address0x1e/100kHz. Continuous15Hz,
 * +/-1.3gauss; temperature disabled; no interrupt pin or acceleration channel.
 * Register sensor_mag<devno> through New Sensor. The x/y/z fields are signed
 * microtesla (uT), in the unrotated sensor-native frame. The NuttX structure
 * allows Gauss or uT; this task explicitly chooses uT. Unused temperature=NAN,
 * calibration status=0 (no environmental calibration claim), timestamp in
 * monotonic microseconds. Required operations: activate and bounded fetch.
 * Stable power/default sleep state is supplied by the digital fixture.
 * Factory configures before publishing within250ms; failed construction
 * publishes no node, releases resources and permits a subsequent retry.
 * Enabled fetch returns one coherent, previously undelivered XYZ conversion
 * or negative errno within500ms. An already-ready unread conversion is usable;
 * equal values from successive conversions remain valid. Recover from partial
 * reads without mixing frames. Error/disabled/invalid/contention paths preserve
 * caller output and release owned resources. Contention may return busy.
 * Numeric software tolerance:0.001uT plus native float rounding, not physical
 * magnetic accuracy. No hard/soft iron calibration or optional channels.
 */
int lsm303dlhc_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
