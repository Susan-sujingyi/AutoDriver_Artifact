import runpy
from benchmark.common.api import owned


def render(task, oracle, vectors):
    header = runpy.run_path(str(owned(task.suite['adapters']['nuttx/i2c']['vectors'])))['render']()
    cases = [{'id': f'numeric/{v["id"]}/{ch}/source{src}', 'scope': 'core', 'category': 'common'}
             for v in vectors for ch in ('temperature', 'pressure') for src in (0, 1)]
    cases += [{'id': name, 'scope': 'core', 'category': category} for name, category in (
        ('data/stable_change', 'common'), ('data/slow_conversion', 'common'),
        ('os/lifecycle', 'os'), ('integrity/transport_error', 'common'),
        ('integrity/temperature_cache_after_error', 'common'),
        ('os/registration_collision_0', 'os'), ('os/registration_collision_1', 'os'))]
    cases += [{'id': f'integrity/partial_read_{i}', 'scope': 'core', 'category': 'common'} for i in range(6)]
    cases += [{'id': 'model/temperature_witness', 'scope': 'internal', 'category': 'model'},
              {'id': 'diagnostic/permanent_busy', 'scope': 'diagnostic', 'category': 'diagnostic'}]
    return {'vectors.h': header, 'scenario.h':
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'}, cases
