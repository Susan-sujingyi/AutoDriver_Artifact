#!/usr/bin/env python3
"""Offline distribution checks, with optional real native reference builds."""
from pathlib import Path
import argparse
import json
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from benchmark.common.api import resolve_task, task_paths
from benchmark.common.release import verify_release
from benchmark.common.inputs.prepare import prepare_input
from benchmark.common.target.prepare import prepare_target
from tests.common.method import run_method

REPRESENTATIVES = ('zephyr-tock-sht3x-i2c', 'zephyr-nuttx-sht3x-i2c',
                   'riot-tock-adt7310-spi-mode0')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', choices=('none', 'representative', 'all'), default='none')
    parser.add_argument('--native', action='store_true', help='Build/score the three host-only reference fixtures')
    args = parser.parse_args()
    result = verify_release()
    result['input_checks'] = []
    (ROOT / 'run').mkdir(exist_ok=True)
    names = sorted(task_paths()) if args.inputs == 'all' else REPRESENTATIVES if args.inputs == 'representative' else []
    for name in names:
        task = resolve_task(name)
        with tempfile.TemporaryDirectory(dir=ROOT / 'run', prefix='offline-input-') as folder:
            work = Path(folder)
            baseline = work / 'host/baseline'
            prepare_target(task, baseline)
            prepare_input(task, 'SD', baseline, work / 'agent')
            request = dict(schema='autodriver.method-request/v1', operation='offline_contract',
                           task_id=name, condition='SD')
            checked = run_method('autodriver', work / 'agent', request, work / 'host/contract')
            if checked.get('status') != 'pass' or checked.get('result', {}).get('status') != 'offline_contract_validated':
                raise RuntimeError('Public handoff failed: ' + name + ': ' + json.dumps(checked))
            result['input_checks'].append(name)
            print('Public SD handoff passed: ' + name, flush=True)
    if args.native:
        from tests.common.pipeline import reference_trial
        # The temporary output root contains only checks performed now. No
        # experiment record is needed by verification or retained on success.
        (ROOT / 'result/reference').mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'result/reference', prefix='offline-native-') as folder:
            batch = Path(folder).name
            result['native_checks'] = []
            try:
                for name in REPRESENTATIVES:
                    checked = reference_trial(name, batch)
                    result['native_checks'].append(dict(task=name, status=checked['status'],
                                                       native_build=checked.get('native_build_status')))
                    if checked['status'] != 'pass':
                        raise RuntimeError('Reference build/score failed: ' + name + ': ' + json.dumps(checked))
                    print('Native reference build and score passed: ' + name, flush=True)
            finally:
                scratch = ROOT / 'run' / batch
                if scratch.exists():
                    shutil.rmtree(scratch)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
