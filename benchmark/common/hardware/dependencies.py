"""Hardware-only dependencies; never alter the frozen software dependency set."""
import json
import os
from pathlib import Path
from benchmark.common.api import digest, file_map


def verify(root):
    directory = root / 'third_party/hardware_tools'
    manifest = json.loads((directory / 'manifest.json').read_text())
    actual = file_map(directory)
    actual.pop('manifest.json')
    if actual != manifest['files']:
        raise ValueError('Hardware tool source/vendor inventory mismatch')
    user = root / 'third_party/libtock-rs'
    snap = json.loads((root / 'third_party/metadata/libtock-rs_snapshot.json').read_text())
    expected = {v['path']: v['sha256'] for v in snap['files']}
    if file_map(user) != expected:
        raise ValueError('Userspace source snapshot changed')
    return {'manifest_sha256': digest(directory / 'manifest.json'),
            'elf2tab_version': manifest['elf2tab_version'], 'toolchain': manifest['toolchain'],
            'userspace_manifest_sha256': digest(root / 'third_party/metadata/libtock-rs_snapshot.json')}


def environment(root, home):
    home.mkdir(parents=True, exist_ok=False)
    (home / 'config.toml').write_text('[net]\noffline=true\n[source.crates-io]\nreplace-with="bundled"\n[source.bundled]\ndirectory=' + json.dumps(str(root / 'third_party/hardware_tools/vendor')) + '\n')
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'RUSTUP_HOME', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
    env.update(CARGO_HOME=str(home), RUSTUP_TOOLCHAIN='1.88.0', CARGO_INCREMENTAL='0', CARGO_NET_OFFLINE='true')
    return env
