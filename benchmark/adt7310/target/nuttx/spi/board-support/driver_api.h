/* Public native New Sensor factory ABI. No device protocol implementation. */
#ifndef ADT7310_H
#define ADT7310_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/spi/spi.h>
/* Register SENSOR_TYPE_AMBIENT_TEMPERATURE instance devno. Native sensor_temp
 * temperature is degC, timestamp is microseconds. Each enabled fetch waits
 * for a fresh continuous-conversion result, bounded to 1.1 seconds. Factory
 * registration does not sample. Disabled fetches fail without bus activity.
 * Failures return negative errno and release device/bus resources.
 * Product: mode 3, MSB first, 8 bits, <=1 MHz, continuous 16-bit conversion.
 * The board supplies SPI0, SPIDEV_TEMPERATURE(0), active-low CS on P1.04.
 */
int adt7310_register(struct spi_dev_s *spi, uint32_t devid, int devno);
#endif
