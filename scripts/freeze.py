#!/usr/bin/env python3
"""Explicitly snapshot reviewed distribution files; never certify performance."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from benchmark.common.api import identity, inventory, load, save, task_paths, resolve_task
from benchmark.common.source.validate import verify_source
from benchmark.common.freeze_scope import owned_files, scopes_record


def main():
    tasks = {}
    for name in sorted(task_paths()):
        task = resolve_task(name)
        receipt = verify_source(task)
        tasks[name] = dict(task_identity=identity(task.files()),
                           source_receipt_identity=identity(receipt))
    if len(tasks) != 44:
        raise ValueError('The extracted benchmark must contain exactly 44 tasks')
    resources = inventory(ROOT / 'third_party', links=True)
    resources.pop('sources.lock', None)
    resource_path = ROOT / 'third_party/sources.lock'
    resource_record = dict(schema='autodriver.standalone-resources/v1', files=resources)
    if not resource_path.exists() or load(resource_path) != resource_record:
        save(resource_path, resource_record)
    files = owned_files()
    manifest = dict(schema='autodriver.standalone-artifact/v1', status='extracted',
                    revision=1, files=files, resources_identity=identity(resources),
                    tasks=tasks, methods={'autodriver': 'available'},
                    dependency_scopes=scopes_record(files, resources),
                    model_calls=0, hardware_operations=0,
                    scope='Content snapshot only; no inherited performance acceptance or hardware validation')
    manifest['release_identity'] = identity(manifest)
    save(ROOT / 'manifest.json', manifest)
    print(json.dumps(dict(status='pass', tasks=len(tasks), files=len(files),
                          resources=len(resources), release_identity=manifest['release_identity'])))


if __name__ == '__main__':
    main()
