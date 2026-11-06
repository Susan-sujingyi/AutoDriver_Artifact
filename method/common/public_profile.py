"""Visible task material and public compilation, shared by Python adapters."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import yaml


class PublicCompilationTimeout(RuntimeError):
    """Requires environment/reference comparison; not automatically infrastructure."""


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def material(workspace):
    task = yaml.safe_load((workspace / 'task.yaml').read_text())
    public = '\n\n'.join((workspace / p).read_text() for p in ('prompt.md', 'task.yaml'))
    # The public task declares the exact product ABI; never infer header names.
    from method.common.interfaces import read_interfaces
    for name, text in read_interfaces(workspace, task):
        public += '\n\nPublic target integration file ' + name + ':\n' + text
    docs = '\n'.join(str(p.relative_to(workspace)) for p in (workspace / 'documents').glob('*.pdf'))
    entries = task.get('source', {}).get('selected_files', [])
    paths = [entry['path'] for entry in entries]
    paths += task.get('source', {}).get('configuration_files', [])
    sources = '\n\n'.join("FILE: " + p + "\n" + (workspace / p).read_text()
                           for p in dict.fromkeys(paths))
    return task, public, docs, sources


def rust_code(response):
    blocks = re.findall(r'```rust\s*(.*?)```', response, re.S)
    return '\n\n'.join(blocks).strip() if blocks else response.strip()


def compile_candidate(workspace, output, attempt):
    destination = output / f'compile-{attempt}'
    process = subprocess.run([sys.executable, str(workspace / 'support/build.py'),
                              '--workspace', str(workspace), '--output', str(destination)],
                             cwd=workspace, text=True, capture_output=True)
    if not (destination / 'report.json').exists():
        raise RuntimeError('Public build helper failed: ' + process.stderr[-4000:])
    results = json.loads((destination / 'report.json').read_text())
    if any(r.get('timed_out') for r in results):
        raise PublicCompilationTimeout('Public build timed out')
    return results


def entry(run):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        result = run(args.workspace, args.out)
        save(args.out / 'method-result.json', result)
    except Exception as error:
        save(args.out / 'method-result.json', {'status': 'public_compile_timeout_unattributed'
             if isinstance(error, PublicCompilationTimeout) else 'method_error',
             'error_type': type(error).__name__, 'error': str(error)})
        raise


def policy():
    return {"schema": "autodriver.method-material-policy/v1", "scope": "Only the supplied public workspace; no additional OS checkout or benchmark reads"}


def example_files(target):
    """Generic examples from the already sanitized public target only."""
    return sorted(p.relative_to(target).as_posix() for p in (Path(target)/"capsules/extra/src").glob("*.rs") if re.search(r"\bsensors::",p.read_text()))
