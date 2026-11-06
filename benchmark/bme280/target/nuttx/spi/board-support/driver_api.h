/* Public New Sensor factory ABI; no device protocol or compensation code. */
#ifndef BME280_SPI_H
#define BME280_SPI_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/spi/spi.h>

/* Register three native fetch nodes at devno:
 * SENSOR_TYPE_AMBIENT_TEMPERATURE: sensor_temp.temperature, degC;
 * SENSOR_TYPE_RELATIVE_HUMIDITY: sensor_humi.humidity, percent RH;
 * SENSOR_TYPE_BAROMETER: sensor_baro.pressure, hPa (temperature in degC).
 * Each enabled-node fetch obtains a fresh forced sample with a microsecond
 * timestamp. Unused optional fields are NaN. No hardware sampling at factory
 * registration. Failure leaves no partially registered nodes. Invalid inputs,
 * disabled fetches and failed/invalid measurements return negative errno.
 * Operations must release bus/device resources on success and failure.
 * Board supplies SPI0 and SPIDEV_BAROMETER(0), active-low CS on P1.04.
 * Product configuration: mode 3, MSB first, 8 bits, <=1 MHz; temperature x1,
 * pressure x16, humidity x1, filter off. Acquisition is bounded to 1 second.
 */
int bme280_spi_register(struct spi_dev_s *spi, uint32_t devid, int devno);
#endif
