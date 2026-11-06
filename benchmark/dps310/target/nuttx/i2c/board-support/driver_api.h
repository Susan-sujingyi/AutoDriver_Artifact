/* Public native ABI and product policy; no register recipe or compensation. */
#ifndef DPS310_H
#define DPS310_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* Exact DPS310, I2C0/address0x77/100kHz. Temperature and pressure8x,
 * command acquisition; FIFO/background/interrupt acquisition disabled.
 * Register sensor_ambient_temp<devno> and sensor_baro<devno> atomically.
 * Native sensor_temp.temperature is internal die temperature in degC;
 * sensor_baro.pressure is hPa and its unused temperature field is NAN.
 * Timestamps use monotonic microseconds. Factory validates/configures within
 * 100ms in the disclosed digital fixture; failure leaves no nodes and allows
 * retry. Enabled fetch returns one native event or negative errno within100ms.
 * Each requested channel needs a completed conversion after request acceptance.
 * Pressure uses the most recently acquired valid temperature conversion,
 * completed no more than1second apart. Pressure-first requests must work.
 * Failed/partial temperature acquisition invalidates the temperature cache;
 * reset invalidates all caches. Pressure-only errors need not discard valid
 * temperature. Already outstanding device conversions may finish after an
 * error; recover before issuing conflicting commands. No unsolicited events.
 * Preserve caller output on errors, disabled/invalid requests and contention;
 * release resources on every path. Contention may return busy. Numerical
 * allowances are0.01degC/1hPa plus float rounding, not physical accuracy.
 */
int dps310_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
