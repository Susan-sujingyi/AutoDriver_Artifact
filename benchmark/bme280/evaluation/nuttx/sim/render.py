"""Shared BME280 scenarios; bus transport comes only from the frozen task."""
import runpy
from benchmark.common.api import owned


def render(task, oracle, vectors):
    adapter = task.suite['adapters']['nuttx/' + task.bus]
    header, _ = runpy.run_path(str(owned(adapter['vectors'])))['render']()
    spi = task.bus == 'spi'
    if spi:
        params = task.requirements['connection']['spi']
        if params['word_bits'] != 8 or params['bit_order'].lower() != 'msb_first' or params['chip_select'] != 'active_low':
            raise NotImplementedError('BME280 SIM SPI framing')
        config = (f'#define BENCH_SPI_MODE SPIDEV_MODE{params["mode"]}\n'
                  f'#define BENCH_MAX_HZ {params["max_frequency_hz"]}\n')
    else:
        config = (f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
                  '#define BENCH_MAX_HZ 100000\n')
    cases = [{'id': 'numeric/' + v['id'] + '/' + channel, 'scope': 'core', 'category': 'common'}
             for v in vectors for channel in ('temperature', 'humidity', 'pressure')]
    cases += [{'id': name, 'scope': 'core', 'category': category} for name, category in (
        ('data/stable_change', 'common'), ('os/lifecycle', 'os'),
        ('os/invalid_factory', 'os'), ('integrity/transport_error', 'common'),
        ('integrity/invalid_pressure_calibration', 'common'))]
    cases += [{'id': 'os/registration_collision_' + str(ch), 'scope': 'core', 'category': 'os'} for ch in range(3)]
    cases += [{'id': 'integrity/skipped_' + ch, 'scope': 'core', 'category': 'common'}
              for ch in ('temperature', 'humidity', 'pressure')]
    cases += [{'id': 'model/shadow_and_forced', 'scope': 'internal', 'category': 'model'},
              {'id': 'diagnostic/permanent_busy', 'scope': 'diagnostic', 'category': 'diagnostic'},
              {'id': 'diagnostic/nvm_busy', 'scope': 'diagnostic', 'category': 'diagnostic'}]
    return {'vectors.h': header, 'scenario.h': f'#define BENCH_SPI {int(spi)}\n' + config +
            '#define BENCH_VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))\n'}, cases
