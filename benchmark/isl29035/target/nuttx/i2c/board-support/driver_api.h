/* Public New Sensor ABI and necessary product configuration; no protocol. */
#ifndef ISL29035_H
#define ISL29035_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* Register sensor_light<devno>. Native sensor_light.light is lux; the unused
 * IR field is NAN. Timestamp is monotonic microseconds. The TIMER_ARCH clock
 * interpolates the current SysTick counter; the scheduling period is 1 ms.
 * No additional timer is allocated.
 * Exact ISL29035 at 0x44, I2C0 100 kHz, continuous ALS, 4000 lux range,
 * eight-bit ADC/410 us integration, interrupt disabled, persistence one.
 * Factory validates/initializes the device before publishing a node; failure
 * leaves no registered node and can be retried. Startup bound: 100 ms.
 * Each enabled fetch consumes a newer valid conversion. Bound fetch to 10 ms
 * in the digital fixture. No sample is published for invalid buffers, disabled
 * fetches or device/bus errors. Return negative errno and release resources.
 * Concurrent requests may return busy. No optional controls are required.
 */
int isl29035_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
