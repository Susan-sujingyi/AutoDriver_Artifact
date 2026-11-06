"""Native magnetic witnesses share the frozen rational compensation oracle."""
import runpy
from benchmark.common.api import owned


def render(task, oracle, vectors):
    numeric = runpy.run_path(str(owned(task.suite['adapters']['nuttx/i2c']['vectors'])))
    records = numeric['records']()
    special = next(i for i, v in enumerate(records) if v['id'] == 'zero_denominator_recovery')
    cases = [{'id':'numeric/' + v['id'], 'scope':'core', 'category':'common'} for v in records]
    cases += [{'id':name, 'scope':'core', 'category':category} for name,category in (
        ('data/stable_change','common'), ('data/already_ready','common'), ('data/slow_phase','common'),
        ('os/lifecycle','os'), ('os/registration_collision','os'), ('os/invalid_factory','os'),
        ('integrity/transport_error','common'))]
    cases += [{'id':f'integrity/partial_read_{i}', 'scope':'core','category':'common'} for i in range(8)]
    cases += [{'id':f'integrity/invalid_{i}', 'scope':'core','category':'common'} for i in range(1,7)]
    cases += [{'id':f'integrity/trim_{i}', 'scope':'core','category':'common'} for i in range(4)]
    cases += [{'id':'model/shadow_and_ready','scope':'internal','category':'model'},
              {'id':'diagnostic/stalled','scope':'diagnostic','category':'diagnostic'}]
    return {'vectors.h':numeric['render'](), 'scenario.h':
            f'#define BENCH_I2C_ADDRESS {task.requirements["connection"]["i2c"]["address_7bit"]}\n'
            f'#define BENCH_SPECIAL_INDEX {special}\n'},cases
