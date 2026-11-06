/* Generic waveform resource in SIM. All protocol and pulse generation live
 * in device models; buffer packing and physical time are shared here.
 */
#include <assert.h>
#include <errno.h>
#include <string.h>
#include <nuttx/sensors/gpio_waveform.h>
#include "wave.h"
#include "bus.h"

static const struct bench_wave_ops *device;
static void *context;

void bench_wave_install(const struct bench_wave_ops *ops, void *arg)
{
  assert(ops && ops->drive && ops->begin && ops->level && ops->end);
  device = ops;
  context = arg;
}

int gpio_waveform_drive_low(bool low)
{
  return device ? device->drive(context, low) : -ENODEV;
}

int gpio_waveform_capture(uint8_t *buffer, size_t capacity, size_t count,
                          uint32_t period)
{
  int result;
  if (!device)
    {
      return -ENODEV;
    }
  if (!buffer || !count || count > 8000 || capacity < (count + 7) / 8 ||
      !period || period > 100 || count * period > 8000)
    {
      return -EINVAL;
    }
  result = device->begin(context);
  device->drive(context, false);
  if (result < 0)
    {
      return result;
    }
  memset(buffer, 0, (count + 7) / 8);
  for (size_t i = 0; i < count; i++)
    {
      bench_wire_delay(period, 1000000);
      if (device->level(context, (i + 1) * period))
        {
          buffer[i / 8] |= 0x80 >> (i % 8);
        }
    }
  device->end(context);
  return count;
}
