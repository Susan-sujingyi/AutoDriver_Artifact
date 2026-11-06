/* Public native New Sensor ABI and product configuration; no device protocol. */
#ifndef SHT3X_H
#define SHT3X_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* Register sensor_ambient_temp<devno> and sensor_humi<devno> fetch nodes.
 * Native events: sensor_temp.temperature in degC; sensor_humi.humidity in
 * percent RH. Timestamp is monotonic microseconds at completed acquisition.
 * Each enabled-node fetch obtains a fresh single-shot measurement. CRC must
 * protect the requested channel. Failed/invalid measurements, invalid buffers
 * and disabled fetches return negative errno, without publishing a sample.
 * Bound a fetch to 100 ms. Release resources on every return path.
 * Registration performs no measurement and failure leaves no partial nodes.
 * Board: I2C0 at 100 kHz, 7-bit address 0x44, SHT30 exact part.
 * Product: high repeatability, clock stretching disabled, heater disabled.
 */
int sht3x_register(struct i2c_master_s *i2c, uint8_t address, int devno);
#endif
