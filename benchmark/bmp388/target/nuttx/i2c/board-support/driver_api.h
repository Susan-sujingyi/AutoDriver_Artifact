/* Public New Sensor ABI/product configuration only; no register recipe. */
#ifndef BMP388_H
#define BMP388_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* Exact BMP388, I2C0/0x76/100kHz. Normal25Hz, pressure4x, temperature1x,
 * IIR/FIFO/interrupt acquisition off. CSB high, SDO low from power-on.
 * Register sensor_ambient_temp<devno> and sensor_baro<devno> atomically.
 * sensor_temp.temperature is die temperature in degC; sensor_baro.pressure
 * is hPa, with its unused temperature field NAN. Timestamps are monotonic
 * microseconds. Factory validates/configures before publishing within100ms
 * in the digital fixture. Failure publishes no nodes and permits retry.
 * Enabled fetch returns one native event or negative errno within100ms.
 * One coherently acquired epoch may satisfy each channel once. Repeated
 * same-channel reads require a later epoch, including equal numeric values.
 * An already-ready unread epoch is eligible. Pressure uses temperature from
 * the same measurement epoch. Reset/known bus or device errors invalidate
 * cached success. Caller output stays unchanged on invalid/disabled/error
 * requests; release resources on all outcomes. Contention may return busy.
 * Inherited numerical allowances are0.01degC and1hPa, plus native float
 * rounding; they do not establish physical accuracy. No dynamic configuration
 * or extra channels are required.
 */
int bmp388_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
