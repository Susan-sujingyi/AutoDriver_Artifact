/* Public New Sensor factory ABI; no device protocol or compensation code. */
#ifndef BME280_H
#define BME280_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>

/* Register three native fetch nodes at devno:
 * SENSOR_TYPE_AMBIENT_TEMPERATURE: sensor_temp.temperature, degC;
 * SENSOR_TYPE_RELATIVE_HUMIDITY: sensor_humi.humidity, percent RH;
 * SENSOR_TYPE_BAROMETER: sensor_baro.pressure, hPa (temperature in degC).
 * Each enabled-node fetch obtains a fresh forced sample with a microsecond
 * timestamp. Unused optional fields are NaN. No hardware sampling at factory
 * registration. Failure leaves no partially registered nodes. Invalid inputs,
 * disabled fetches and failed/invalid measurements return negative errno.
 * Operations must release bus/device resources on success and failure.
 * Board supplies I2C0 and 7-bit address 0x76.
 * Product configuration: 100 kHz I2C; temperature x1,
 * pressure x16, humidity x1, filter off. Acquisition is bounded to 1 second.
 */
int bme280_register(struct i2c_master_s *i2c, uint8_t address, int devno);
#endif
