/* Public New Sensor registration ABI; no device protocol implementation. */
#ifndef DHT11_H
#define DHT11_H
#include <nuttx/config.h>
#include <nuttx/sensors/gpio_waveform.h>

/* Register SENSOR_TYPE_AMBIENT_TEMPERATURE and SENSOR_TYPE_RELATIVE_HUMIDITY
 * at devno. Native sensor_temp.temperature is degC; sensor_humi.humidity is
 * percent RH. Each enabled-node fetch reads a fresh device frame and provides
 * a microsecond timestamp. No hidden caching across logical node reads.
 * Factory registration must not acquire a frame; acquisition may wait for
 * device startup or sampling interval, but must finish within 2 seconds.
 * Errors return negative errno, never invented measurements. Failed factory
 * registration leaves no nodes. Disabled fetches fail without wire activity.
 * The generic board waveform resource is available through gpio_waveform.h.
 * Driver owns frame interpretation, checksums, and device-specific sequencing.
 */
int dht11_register(int devno);
#endif
