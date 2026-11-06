#!/usr/bin/env python3
"""Install only bundled wheels, including release validators, in a local venv."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if sys.version_info[:2] != (3, 13):
    raise SystemExit('This release uses Python 3.13 wheels')
env = ROOT / '.venv'
if not env.exists():
    subprocess.run([sys.executable, '-m', 'venv', str(env)], check=True)
wheels = sorted({p.parent for directory in ('third_party/wheels', 'third_party/hardware_tools/wheels')
                 for p in (ROOT / directory).rglob('*.whl')})
base = [str(env / 'bin/python'), '-m', 'pip', 'install', '--no-index', '--no-cache-dir']
for directory in wheels:
    base += ['--find-links', str(directory)]
subprocess.run(base + ['-r', str(ROOT / 'dependencies/requirements.lock'),
    'kconfiglib==14.1.0',
    'pyelftools==0.32', 'pyserial==3.5', 'setuptools==78.1.1', 'wheel==0.45.1'], check=True)
subprocess.run(base + ['--no-deps', '--require-hashes', '-r',
    str(ROOT / 'dependencies/requirements-autodriver.lock')], check=True)
# Hash-checked source-validator dependencies use their own invocation; pip's
# require-hashes mode must not leak onto the separately locked method packages.
subprocess.run(base + ['--require-hashes', '-r', str(ROOT / 'benchmark/common/source/zephyr/fixture/requirements.lock')], check=True)
subprocess.run(base + ['--no-deps', '--no-build-isolation', '-e', str(ROOT)], check=True)
