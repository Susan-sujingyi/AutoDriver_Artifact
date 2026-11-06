"""Content identities for the standalone benchmark and AutoDriver runtime.

Generated trials and knowledge are excluded. Verification never regenerates
identities, compiles source inputs, calls a model, or reads another checkout.
"""
import os
from pathlib import Path

from benchmark.common.api import ROOT, digest, identity, load

METHODS = ('autodriver',)
COMMON = ('benchmark', 'tests', 'method/common', 'dependencies', 'scripts', 'docs', 'examples')
TOP_LEVEL = ('AGENTS.md', 'README.md', 'THIRD_PARTY_NOTICES.md',
             'pyproject.toml', '.gitignore', '.env.example',
             'EXTRACTION.json', 'third_party/sources.lock')


def scope_for(name):
    return 'autodriver' if name.startswith('method/autodriver/') else 'common'


def paths(root=ROOT, scopes=None, *, resources=True):
    root = Path(root)
    scopes = set(scopes if scopes is not None else ('common', *METHODS))
    if not scopes <= {'common', *METHODS}:
        raise ValueError('Unknown dependency scope')
    bases = [*COMMON, 'method/autodriver'] + (['third_party'] if resources else [])
    found = set()
    for base in bases:
        if scope_for(base + '/') not in scopes:
            continue
        directory = root / base
        if directory.is_symlink():
            raise ValueError('Linked dependency root: ' + base)
        if not directory.exists():
            continue
        for current, dirs, files in os.walk(directory, followlinks=False):
            parent = Path(current)
            dirs[:] = [n for n in dirs if n not in {'__pycache__', '.git'}
                       and (parent / n).relative_to(root).as_posix()
                       != 'method/autodriver/knowledge']
            linked = [n for n in dirs if (parent / n).is_symlink()]
            for name in linked + files:
                p = parent / name
                key = p.relative_to(root).as_posix()
                if p.suffix in {'.pyc', '.pyo'} or key == 'third_party/sources.lock':
                    continue
                if p.is_symlink() and (not key.startswith('third_party/')
                                       or not p.resolve().is_relative_to((root / 'third_party').resolve())):
                    raise ValueError('Linked or external frozen material: ' + key)
                found.add(key)
    if 'common' in scopes:
        for name in TOP_LEVEL:
            p = root / name
            if p.exists():
                if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
                    raise ValueError('Linked or external frozen material: ' + name)
                found.add(name)
    return sorted(found)


def owned_files(root=ROOT, scopes=None):
    root = Path(root)
    return {n: digest(root / n) for n in paths(root, scopes, resources=False)}


def scopes_record(files, resource_files):
    result = {}
    for scope in ('common', *METHODS):
        selected = {k: v for k, v in files.items() if scope_for(k) == scope}
        resources = {k: v for k, v in resource_files.items()
                     if scope_for('third_party/' + k) == scope}
        record = dict(files_identity=identity(selected), file_count=len(selected),
                      resources_identity=identity(resources), resource_count=len(resources))
        record['identity'] = identity(record)
        result[scope] = record
    return result


def read_manifest(root=ROOT):
    root = Path(root)
    path = root / 'manifest.json'
    if path.is_symlink():
        raise ValueError('Linked manifest')
    manifest = load(path)
    if (manifest.get('schema') != 'autodriver.standalone-artifact/v1'
            or manifest.get('status') != 'extracted'
            or manifest.get('release_identity') != identity(
                {k: v for k, v in manifest.items() if k != 'release_identity'})):
        raise ValueError('Invalid standalone artifact manifest')
    return manifest


def verify_scope(scope, root=ROOT):
    if scope not in {'common', *METHODS}:
        raise ValueError('Unknown dependency scope')
    root = Path(root)
    manifest = read_manifest(root)
    expected = manifest['dependency_scopes'][scope]
    files = owned_files(root, [scope])
    if identity(files) != expected['files_identity'] or len(files) != expected['file_count']:
        raise ValueError('Frozen ' + scope + ' materials changed; review and run scripts/freeze.py')
    actual = {}
    for name in paths(root, [scope]):
        if not name.startswith('third_party/') or name == 'third_party/sources.lock':
            continue
        p = root / name
        actual[name[len('third_party/'):]] = (
            {'symlink': os.readlink(p)} if p.is_symlink() else
            {'sha256': digest(p), 'executable': bool(p.stat().st_mode & 0o111)})
    if identity(actual) != expected['resources_identity'] or len(actual) != expected['resource_count']:
        raise ValueError('Frozen ' + scope + ' resources changed')
    if scope == 'common':
        from benchmark.common.api import task_paths, resolve_task
        from benchmark.common.source.validate import verify_source
        if set(task_paths()) != set(manifest['tasks']):
            raise ValueError('Task population changed')
        for name in task_paths():
            task = resolve_task(name)
            receipt = verify_source(task)
            if (identity(task.files()) != manifest['tasks'][name]['task_identity']
                    or identity(receipt) != manifest['tasks'][name]['source_receipt_identity']):
                raise ValueError('Task or source receipt changed: ' + name)
    return dict(status='pass', release_identity=manifest['release_identity'],
                scope=scope, scope_identity=expected['identity'], source_compilations=0)


def verify_experiment_release(method, binding=None):
    if method not in METHODS:
        raise ValueError('Only AutoDriver is included')
    checked = [verify_scope(s) for s in ('common', method)]
    result = dict(status='pass', release_identity=checked[0]['release_identity'],
                  dependency_identities={r['scope']: r['scope_identity'] for r in checked},
                  source_compilations=0)
    if binding is not None and binding != result['dependency_identities']:
        raise ValueError('Experiment dependency binding changed')
    return result
