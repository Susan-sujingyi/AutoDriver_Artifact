"""Host-owned startup wrapper for final firmware; frozen agent inputs stay intact."""
from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile

from benchmark.common.api import digest


BOOT_SOURCE = r'''/* AutoDriver firmware startup integration; no sensor protocol. */
#include <stdio.h>
#include <unistd.h>

int sensor_sample_main(int argc, char **argv);
int nsh_main(int argc, char **argv);

struct sensor_sample_boot_plan_s
{
  unsigned int delay_seconds;
  const char *sample_count;
  int (*sample)(int, char **);
  int (*shell)(int, char **);
};

const struct sensor_sample_boot_plan_s g_sensor_sample_boot_plan =
  {5, "100", sensor_sample_main, nsh_main};

int sensor_sample_boot_main(int argc, char **argv)
{
  const volatile struct sensor_sample_boot_plan_s *plan = &g_sensor_sample_boot_plan;
  unsigned int remaining = plan->delay_seconds;
  char *sample_argv[] = {"sensor_sample", (char *)plan->sample_count, NULL};
  int status;

  /* Complete the delay even if sleep is interrupted by a signal. */
  while (remaining != 0)
    {
      remaining = sleep(remaining);
    }

  status = plan->sample(2, sample_argv);
  printf("sensor_sample: automatic sampling finished (status=%d); entering NSH\n",
         status);
  fflush(stdout);
  return plan->shell(argc, argv);
}
'''


def install_boot(tree: Path) -> None:
    app = tree / 'apps/system/sensor_sample'
    makefile = app / 'Makefile'
    anchor = 'include $(APPDIR)/Application.mk'
    text = makefile.read_text()
    if text.count(anchor) != 1 or (app / 'sensor_sample_boot.c').exists():
        raise ValueError('Unexpected startup integration preimage')
    (app / 'sensor_sample_boot.c').write_text(BOOT_SOURCE)
    makefile.write_text(text.replace(anchor, 'CSRCS += sensor_sample_boot.c\n' + anchor))


def verify_boot(elf: Path, config: Path) -> dict:
    """Check the linked boot plan and selected init entry, not runtime behavior.

    verify_command separately binds this ELF to the actual flash BIN.
    """
    lines = set(config.read_text().splitlines())
    required = {'CONFIG_INIT_ENTRY=y', 'CONFIG_BOARD_LATE_INITIALIZE=y',
                'CONFIG_INIT_ENTRYPOINT="sensor_sample_boot_main"', 'CONFIG_INIT_STACKSIZE=4096',
                'CONFIG_BUILD_FLAT=y'}
    if not required <= lines:
        raise ValueError('Automatic startup configuration missing or changed')
    with elf.open('rb') as stream:
        image = ELFFile(stream)
        if image.elfclass != 32 or not image.little_endian or image['e_machine'] != 'EM_ARM':
            raise ValueError('Expected ARM32 boot plan')
        table = image.get_section_by_name('.symtab')
        symbols = {s.name: s for s in table.iter_symbols()
                   if s['st_shndx'] != 'SHN_UNDEF'} if table else {}
        names = ('sensor_sample_boot_main', 'sensor_sample_main', 'nsh_main', 'g_sensor_sample_boot_plan', 'nx_start_task')
        if any(n not in symbols for n in names):
            raise ValueError('Final ELF lacks automatic startup symbols')

        def read(address, size):
            for segment in image.iter_segments():
                start = segment['p_vaddr']
                if segment['p_type'] == 'PT_LOAD' and start <= address and address + size <= start + segment['p_filesz']:
                    return segment.data()[address - start:address - start + size]
            raise ValueError('Boot plan points outside ELF data')

        plan = symbols['g_sensor_sample_boot_plan']
        if plan['st_size'] != 16:
            raise ValueError('Unsupported boot plan ABI')
        delay, count, sample, shell = struct.unpack('<4I', read(plan['st_value'], 16))
        if (delay != 5 or read(count, 4) != b'100\0'
                or sample != symbols['sensor_sample_main']['st_value']
                or shell != symbols['nsh_main']['st_value']):
            raise ValueError('Automatic startup plan differs')
        # Pinned GCC Cortex-M4 build loads the init entry from its literal pool.
        # Checking it prevents an edited config from relabelling an old image.
        start = symbols['nx_start_task']
        if struct.pack('<I', symbols['sensor_sample_boot_main']['st_value']) not in read(
                start['st_value'] & ~1, start['st_size']):
            raise ValueError('Kernel init task does not reference the automatic entry')
    return {'status': 'pass', 'entry': 'sensor_sample_boot_main', 'delay_seconds': delay,
            'sample_rounds': 100, 'after_sampling': 'nsh_main',
            'elf_sha256': digest(elf), 'config_sha256': digest(config),
            'scope': 'Static final-image init entry and boot plan; physical startup not run'}
