"""Verify bundled crates and configure a private, offline Cargo home.

This leaves the installed Rust toolchain and all shared Cargo configuration alone.
Only crates.io is replaced; this is not a network/process sandbox.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

MANIFEST = 'benchmark/common/target/tock/dependencies/dependencies.json'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file(root: Path, name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Non-relative bundled dependency: ' + name)
    path = root / relative
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError('Missing or linked bundled dependency: ' + name)
    return path


def verify_vendor(root: Path) -> dict:
    root = root.resolve()
    manifest = _file(root, MANIFEST)
    value = json.loads(manifest.read_text())
    if value.get('schema') != 'driverbench.rust_vendor/v1':
        raise ValueError('Unsupported Rust vendor manifest')
    for item in value.get('supplemental_license_files', []):
        if _sha(_file(root, item['path'])) != item['sha256']:
            raise ValueError('Supplemental license checksum mismatch: ' + item['path'])
    packages = []
    names = {Path(package['source_dir']).name for package in value['packages']}
    sources = root / 'third_party/rust_vendor/sources'
    if {p.name for p in sources.iterdir()} != names:
        raise ValueError('Unexpected or missing crate in vendor source directory')
    for package in value['packages']:
        archive = _file(root, package['archive'])
        if _sha(archive) != package['sha256']:
            raise ValueError('Crate archive checksum mismatch: ' + package['archive'])
        directory = root / package['source_dir']
        expected = package['files']
        actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
        if actual != set(expected) | {'.cargo-checksum.json'}:
            raise ValueError('Vendor source inventory mismatch: ' + package['source_dir'])
        for name, expected_sha in expected.items():
            path = _file(root, (Path(package['source_dir']) / name).as_posix())
            if _sha(path) != expected_sha:
                raise ValueError('Vendor source checksum mismatch: ' + str(path))
        checksum = _file(root, package['source_dir'] + '/.cargo-checksum.json')
        if (_sha(checksum) != package['checksum_file_sha256']
                or json.loads(checksum.read_text()) != {'files': expected, 'package': package['sha256']}):
            raise ValueError('Cargo checksum metadata mismatch: ' + str(checksum))
        packages.append({'name': package['name'], 'version': package['version'],
                         'archive_sha256': package['sha256'], 'verified_files': len(expected)})
    return {'manifest_path': MANIFEST, 'manifest_sha256': _sha(manifest),
            'toolchain': value['toolchain'], 'packages': packages}


def create_cargo_environment(root: Path, cargo_home: Path, environment=None) -> tuple[dict, dict]:
    """Create a new Cargo home; callers reuse the returned env for their commands.

    Existing directories are refused to distinguish this from a warm-cache test.
    Toolchain binaries/rust-src remain public preinstalled prerequisites.
    """
    root = root.resolve()
    cargo_home = cargo_home.resolve()
    evidence = verify_vendor(root)
    cargo_home.mkdir(parents=True, exist_ok=False)
    vendor = root / 'third_party/rust_vendor/sources'
    config = ('[net]\noffline = true\n[source.crates-io]\n'
              'replace-with = "autodriver-vendored"\n[source.autodriver-vendored]\n'
              'directory = ' + json.dumps(str(vendor)) + '\n')
    (cargo_home / 'config.toml').write_text(config)
    env = dict(os.environ if environment is None else environment)
    # Avoid inherited Cargo overrides, compiler wrappers and old output caches.
    for name in list(env):
        if (name.startswith('CARGO_') or name in {'RUSTFLAGS', 'RUSTDOCFLAGS',
                'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTC', 'RUSTDOC'}):
            env.pop(name, None)
    env.update(CARGO_HOME=str(cargo_home), CARGO_NET_OFFLINE='true',
               RUSTUP_TOOLCHAIN=evidence['toolchain'])
    evidence.update(cargo_home=str(cargo_home), initial_cargo_home='absent before configuration',
                    config_sha256=_sha(cargo_home / 'config.toml'),
                    toolchain_scope='Preinstalled Rust toolchain/rust-src retained; only Cargo home is new.')
    return env, evidence
