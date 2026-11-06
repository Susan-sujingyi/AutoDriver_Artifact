/* Public New Sensor ABI and product policy, without a register recipe. */
#ifndef BMM150_H
#define BMM150_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* BMM150 magnetic XYZ, I2C0/address0x13/100kHz (confirmed product address).
 * Normal10Hz,9XY/15Z repetitions, all axes enabled, no interrupt pin.
 * Register sensor_mag<devno> through New Sensor. Signed x/y/z in microtesla
 * (uT), unrotated sensor-native frame. Unused temperature=NAN, calibration
 * status=0 (no environmental calibration claim), monotonic timestamp in us.
 * Required operations: activate and bounded fetch. Factory configures before
 * publishing within100ms from stable power, releases resources on failure and
 * permits retry. Enabled fetch returns one coherent, previously undelivered
 * conversion or negative errno within250ms. Already-ready unread conversions
 * are usable; equal values from successive conversions remain valid. Invalid
 * samples, transport errors, disabled/invalid/contention paths preserve caller
 * output and release owned resources. Contention may return busy. Reject
 * unusable calibration and invalid/overflow measurements; full-width Z and
 * factory Hall compensation are required. Native output range corresponds to
 * signed32-bit nanotesla before conversion to uT. Numeric software allowance:
 * 0.125uT plus native float rounding; this is not physical magnetic accuracy.
 * No environmental calibration, temperature channel or optional self-test.
 */
int bmm150_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
