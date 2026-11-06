/* SPDX-License-Identifier: Apache-2.0
 * Generic digital input sampling for the fixed nRF52840 DK product wiring.
 * No frame recognition, pulse thresholds, checksums or physical conversion.
 */
#include <nuttx/config.h>
#include <nuttx/arch.h>
#include <nuttx/irq.h>
#include <nuttx/mutex.h>
#include <errno.h>
#include <string.h>
#include "nrf52_gpio.h"
#include <nuttx/sensors/gpio_waveform.h>

#ifndef CONFIG_ARCH_PERF_EVENTS
# error "Wave capture requires OS-initialized DWT performance counter"
#endif
#if CONFIG_USEC_PER_TICK < 10000
# error "Bounded capture profile requires SysTick period >= 10 ms"
#endif

#define WIRE_LOW (GPIO_OUTPUT | GPIO_PORT1 | GPIO_PIN(6))
#define WIRE_FREE (GPIO_INPUT | GPIO_PORT1 | GPIO_PIN(6))
#define WIRE_SENSE (GPIO_INPUT | GPIO_PORT1 | GPIO_PIN(7))
static mutex_t capture_lock = NXMUTEX_INITIALIZER;

int gpio_waveform_drive_low(bool low)
{
  int result = nxmutex_lock(&capture_lock);
  if (result < 0) return result;
  result = nrf52_gpio_config(low ? WIRE_LOW : WIRE_FREE);
  if (result == 0 && low) nrf52_gpio_write(WIRE_LOW, false);
  nxmutex_unlock(&capture_lock);
  return result;
}

int gpio_waveform_capture(uint8_t *buffer, size_t capacity, size_t bit_count,
                         uint32_t sample_period_us)
{
  if (!buffer || !bit_count || bit_count > 8000 || capacity < (bit_count+7)/8 ||
      !sample_period_us || sample_period_us > 100 ||
      (uint64_t)bit_count * sample_period_us > 8000)
    return -EINVAL;

  unsigned long frequency = up_perf_getfreq();
  if (frequency == 0 || frequency > 1000000000 || frequency % 1000000)
    return -ENOTSUP;
  uint32_t step = (frequency / 1000000) * sample_period_us;
  uint32_t probe = (uint32_t)up_perf_gettime();
  up_udelay(1);
  if ((uint32_t)up_perf_gettime() == probe) return -EIO;
  int result = nxmutex_lock(&capture_lock);
  if (result < 0) return result;
  result = nrf52_gpio_config(WIRE_SENSE);
  if (result < 0) goto unlock;
  memset(buffer, 0, (bit_count+7)/8);
  irqstate_t flags = up_irq_save();
  result = nrf52_gpio_config(WIRE_FREE);
  if (result < 0) goto restore;
  uint32_t start = (uint32_t)up_perf_gettime();
  for (size_t i = 0; i < bit_count; i++)
    {
      uint32_t target = (i+1)*step;
      uint32_t elapsed;
      do { elapsed = (uint32_t)up_perf_gettime() - start; }
      while (elapsed < target);
      /* Less than one sample period of lateness, including GPIO read overhead,
       * is required. The bound is rechecked after reading the input. */
      bool high = nrf52_gpio_read(WIRE_SENSE);
      elapsed = (uint32_t)up_perf_gettime() - start;
      if (elapsed-target >= step)
        { result = -EOVERFLOW; goto restore; }
      if (high) buffer[i/8] |= 1u << (7-(i%8));
    }
  result = bit_count;
restore:
  nrf52_gpio_config(WIRE_FREE);
  up_irq_restore(flags);
unlock:
  nrf52_gpio_config(WIRE_FREE);
  nxmutex_unlock(&capture_lock);
  return result;
}
