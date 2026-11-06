"""Benchmark-owned Rust fixtures, selected explicitly for a task's real mode."""
import importlib.util
import sys
from benchmark.common.api import ROOT, owned


def available(task):
    if task.os != 'tock': raise ValueError('Tock fixtures only')
    source = owned(next(iter(task.spec['reference']['files'].values()))).read_text()
    directory = ROOT / f'benchmark/{task.sensor}/evaluation/controls'
    spi = task.sensor == 'bme280' and task.bus == 'spi'
    path = directory / ('spi_controls.py' if spi else task.sensor + '_v2_controls.py')
    mode = task.requirements.get('connection', {}).get('spi', {}).get('mode')
    # Legacy sensor fixture modules import their own sibling mutation helpers.
    # These are host-only benchmark code, never candidate imports.
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location('_host_control_' + task.sensor, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, 'candidates'):
            values = {n: (s, e) for n, s, e, *_ in module.candidates(source) if n != 'reference'}
        elif spi:
            values = module.controls(task.sensor, source, mode=mode)
            values.pop('reference')
        else:
            kwargs = {'mode': mode} if task.sensor == 'adt7310' else {}
            values = {n: (s, 'pass') for n, s in module.legal_controls(source, **kwargs).items()}
            values.update({n: v if isinstance(v, tuple) else (v, 'fail')
                           for n, v in module.fault_controls(source, **kwargs).items()})
    finally:
        sys.path.pop(0)
    return source, values


def variants(task):
    from .selection import plan
    source, values = available(task)
    selected = []
    for entry in plan(task):
        if entry['generator'] != 'tock_fixture': raise ValueError('Unapproved Tock control generator')
        text, expected = values[entry['name']]
        if expected != entry['expected'] or text == source:
            raise ValueError('Control expectation or source does not match declared fixture')
        selected.append((entry['name'], text, expected))
    return selected
