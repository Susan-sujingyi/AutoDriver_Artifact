/* Public New Sensor custom-event ABI and necessary product configuration. */
#ifndef APDS9960_H
#define APDS9960_H
#include <nuttx/config.h>
#include <stdint.h>
#include <nuttx/i2c/i2c_master.h>
/* Preserve unsigned raw device counts. Standard sensor_prox is centimetres
 * and is not the event type for this task. No count-to-distance conversion. */
struct custom_proximity_raw { uint64_t timestamp; uint8_t raw; };
/* Register /dev/uorb/proximity_raw<devno> with SENSOR_TYPE_CUSTOM using
 * sensor_custom_register and sizeof(struct custom_proximity_raw).
 * Exact APDS9960 at 0x39, logical I2C0 100 kHz. Continuous polling proximity,
 * gain 2x, eight 8-us pulses, 100 mA drive code, 150% boost, zero offsets,
 * all four photodiodes; ALS/gesture/wait/interrupt sampling disabled.
 * Initialize and validate before publishing the node. Startup and enabled
 * fetch bounds are 100 ms in the digital fixture; physical validation separate.
 * Each fetch returns one coherent, newer valid conversion, including 0 and 255,
 * with a monotonic microsecond timestamp. A first unread ready sample is valid.
 * Disabled/invalid/error requests publish no sample and return negative errno.
 * Factory failure leaves no node and can be retried. Release resources on every
 * terminal path; recover after transient errors. Concurrent requests may return
 * busy. No optional controls or interrupt sampling interface is required.
 */
int apds9960_register(struct i2c_master_s *i2c,uint8_t address,int devno);
#endif
