/* Public New Sensor ABI/product configuration only; no register algorithm. */
#ifndef CCS811_H
#define CCS811_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* CCS811, I2C0/0x5a/100kHz; nWAKE held low, nRESET deasserted, no IRQ.
 * Installed application2.0.0, continuous drive mode1 (one-second cadence).
 * Register sensor_co2<devno> and sensor_tvoc<devno> atomically via New Sensor.
 * sensor_co2.co2 is equivalent CO2 in ppm; sensor_tvoc.tvoc is TVOC in ppm.
 * This task fixes ppm for TVOC following the native struct field definition.
 * Both use monotonic microsecond timestamps. Gas estimates do not measure
 * physical gas concentration accuracy or replace conditioning/calibration.
 * Factory validates/configures before publishing nodes, within100ms in the
 * digital fixture; failures leave no nodes and permit retry.
 * Enabled fetch returns one native event or negative errno within2100ms.
 * Each acquired, coherent gas epoch may satisfy each channel once. Repeated
 * requests on one channel need a new epoch; equal values in new epochs are
 * valid. Known device/bus errors invalidate cached samples. Only the requested
 * channel's range is required to be valid. Disabled/invalid/error fetch leaves
 * the caller buffer unchanged and releases owned resources. Concurrent calls
 * may return busy. Environmental updates, baseline persistence, raw ADC,
 * dynamic modes and firmware installation are not required.
 */
int ccs811_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
