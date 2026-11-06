/* Public lower-half factory ABI. No device register or conversion logic. */
#ifndef BH1750_H
#define BH1750_H

#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>

/* BH1750: registers SENSOR_TYPE_LIGHT instance devno. Standard native event
 * sensor_light.light uses lux; unused IR is NaN. Timestamp is in microseconds.
 * The factory does not sample. Opening enables the device. Reads acquire one
 * fresh sample via fetch; bus errors return a negative errno, never fake lux.
 * Configuration: I2C addr supplied by board; 100 kHz; ordinary high resolution,
 * one-time measurement; MTreg reset default 69. Board power reset is complete.
 */
int bh1750_register(struct i2c_master_s *i2c, uint8_t address, int devno);

#endif
