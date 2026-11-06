"""Trusted final-image DWT lifecycle repair; never installed in agent inputs.

The frozen generic resource and candidate protocol remain unchanged. This
versioned build overlay repairs a host/debugger interaction and adds bounded
failure diagnostics. It contains no sensor decoding or calibrated thresholds.
"""
from pathlib import Path
import hashlib

BASE_SHA256 = '631674d7c63c0045d0df2db1f0417e87cecf635e475123e63dffa666f7ba3184'
RESOURCE = Path('nuttx/boards/arm/nrf52/nrf52840-dk/src/gpio_waveform.c')

CLOCK_HELPER = r'''
/* Final-image integration r5: preserve the shared counter value. */
#include <stdio.h>
#include "arm_internal.h"
#include "nvic.h"
#include "dwt.h"

struct wave_clock_diagnostic
{
  uint32_t demcr_before, ctrl_before, count_before, count_after;
  bool repaired;
};

static int wave_clock_ready(unsigned long frequency,
                            struct wave_clock_diagnostic *diag)
{
  diag->demcr_before = getreg32(NVIC_DEMCR);
  diag->ctrl_before = getreg32(DWT_CTRL);
  diag->count_before = (uint32_t)up_perf_gettime();
  up_udelay(1);
  diag->count_after = (uint32_t)up_perf_gettime();
  if (!(diag->demcr_before & NVIC_DEMCR_TRCENA) ||
      !(diag->ctrl_before & DWT_CTRL_CYCCNTENA_MASK) ||
      diag->count_before == diag->count_after)
    {
      /* OS implementation enables trace and DWT without clearing CYCCNT. */
      up_perf_init((void *)(uintptr_t)frequency);
      diag->repaired = true;
      uint32_t probe = (uint32_t)up_perf_gettime();
      up_udelay(1);
      diag->count_after = (uint32_t)up_perf_gettime();
      if (!(getreg32(NVIC_DEMCR) & NVIC_DEMCR_TRCENA) ||
          !(getreg32(DWT_CTRL) & DWT_CTRL_CYCCNTENA_MASK) ||
          diag->count_after == probe)
        return -EIO;
    }
  return 0;
}

static void wave_report(const char *stage, int result, size_t sample,
                        const struct wave_clock_diagnostic *diag)
{
  /* Called only after releasing the wire and restoring interrupts. Plain
   * diagnostic text is retained in raw logs; client JSON is unchanged. */
  if (result < 0 || diag->repaired)
    printf("gpio_waveform: stage=%s result=%d sample=%lu recovery_attempted=%u "
           "demcr_before=%08lx ctrl_before=%08lx count_before=%lu "
           "count_after=%lu demcr_now=%08lx ctrl_now=%08lx\n",
           stage, result, (unsigned long)sample, (unsigned)diag->repaired,
           (unsigned long)diag->demcr_before, (unsigned long)diag->ctrl_before,
           (unsigned long)diag->count_before, (unsigned long)diag->count_after,
           (unsigned long)getreg32(NVIC_DEMCR),
           (unsigned long)getreg32(DWT_CTRL));
}
'''


def render(source: str) -> str:
    if hashlib.sha256(source.encode()).hexdigest() != BASE_SHA256:
        raise ValueError('DWT resource preimage differs from frozen v1')

    def change(old: str, new: str) -> None:
        nonlocal source
        if source.count(old) != 1:
            raise ValueError('DWT integration anchor differs')
        source = source.replace(old, new)

    change('#define WIRE_LOW', CLOCK_HELPER + '\n#define WIRE_LOW')
    change('  uint32_t probe = (uint32_t)up_perf_gettime();\n'
           '  up_udelay(1);\n'
           '  if ((uint32_t)up_perf_gettime() == probe) return -EIO;\n',
           '  struct wave_clock_diagnostic diagnostic = {0};\n'
           '  const char *stage = "clock_prepare";\n'
           '  size_t sample = 0;\n')
    change('  result = nrf52_gpio_config(WIRE_SENSE);',
           '  result = wave_clock_ready(frequency, &diagnostic);\n'
           '  if (result < 0) goto unlock;\n'
           '  stage = "sense_config";\n'
           '  result = nrf52_gpio_config(WIRE_SENSE);')
    change('  result = nrf52_gpio_config(WIRE_FREE);',
           '  stage = "wire_release";\n'
           '  result = nrf52_gpio_config(WIRE_FREE);')
    change('      uint32_t target = (i+1)*step;\n'
           '      uint32_t elapsed;\n'
           '      do { elapsed = (uint32_t)up_perf_gettime() - start; }\n'
           '      while (elapsed < target);',
           '      sample = i;\n'
           '      uint32_t target = (i+1)*step;\n'
           '      uint32_t elapsed;\n'
           '      uint32_t polls = 0;\n'
           '      do\n'
           '        {\n'
           '          elapsed = (uint32_t)up_perf_gettime() - start;\n'
           '          /* More polls than clock cycles in a period means the\n'
           '           * clock stopped or moved backwards. Never spin forever. */\n'
           '          if (++polls > step + 32)\n'
           '            { stage = "clock_stalled"; result = -EIO; goto restore; }\n'
           '        }\n'
           '      while (elapsed < target);')
    change('{ result = -EOVERFLOW; goto restore; }',
           '{ stage = "sample_late"; result = -EOVERFLOW; goto restore; }')
    change('  result = bit_count;', '  stage = "capture_complete";\n  result = bit_count;')
    change('unlock:\n  nrf52_gpio_config(WIRE_FREE);\n'
           '  nxmutex_unlock(&capture_lock);\n  return result;\n}',
           'unlock:\n  nrf52_gpio_config(WIRE_FREE);\n'
           '  nxmutex_unlock(&capture_lock);\n'
           '  wave_report(stage, result, sample, &diagnostic);\n'
           '  return result;\n}')
    return source


def install_wave(tree: Path) -> dict:
    path = tree / RESOURCE
    source = render(path.read_text())
    path.write_text(source)
    return {'revision': 'dwt-lifecycle-r5', 'path': RESOURCE.as_posix(),
            'preimage_sha256': BASE_SHA256,
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'scope': 'Trusted final firmware only; frozen agent resource unchanged',
            'physical_validation': 'not_run'}
