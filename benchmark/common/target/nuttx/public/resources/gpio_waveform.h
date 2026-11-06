/* Generic board-bound digital waveform resource; no sensor protocol. */
#ifndef GPIO_WAVEFORM_H
#define GPIO_WAVEFORM_H
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/* Product wiring: drive P1.06 (low or high impedance), sense P1.07; external
 * pull-up. P1.08 is reserved/unconnected and not driven by this implementation.
 * Pins belong exclusively to the selected task. Calls must be serialized by
 * the device. This resource has no knowledge of device start pulses or frames.
 */
int gpio_waveform_drive_low(bool low);

/* Release the output, then sample sense into MSB-first packed bits, with the
 * first bit at sample_period_us after release. Exactly bit_count bits are
 * sampled. Buffer capacity is in bytes; final unused bits are zero.
 * 1 <= period <= 100 us, 1 <= bit_count <= 8000 and total duration <= 8000 us.
 * Success returns bit_count, otherwise negative errno. Failure makes buffer
 * contents unusable. The line is released on every return after acquisition.
 *
 * Uses the OS-initialized DWT cycle counter without resetting it. No TIMER,
 * PPI, GPIOTE, SPI or DMA allocation. Interrupts are masked only for the bounded
 * capture window and restored on every exit. This profile requires the 10 ms
 * SysTick period; UART reception can be delayed during capture. A late sample
 * is reported as an error rather than silently returning a distorted trace.
 */
int gpio_waveform_capture(uint8_t *buffer, size_t capacity, size_t bit_count,
                         uint32_t sample_period_us);
#endif
