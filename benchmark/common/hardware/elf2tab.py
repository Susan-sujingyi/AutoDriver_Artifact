import fcntl
import json
import shutil
from benchmark.common.api import digest, save
from benchmark.common.execution import run_logged
from . import dependencies

def tools_binary(root, evidence, logs):
    cache = root / 'run/cache/elf2tab'
    cache.parent.mkdir(parents=True, exist_ok=True)
    with (cache.parent / 'elf2tab.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        receipt = cache / 'receipt.json'; binary = cache / 'elf2tab'
        if receipt.is_file():
            value = json.loads(receipt.read_text())
            if value['source_manifest'] == evidence['manifest_sha256'] and binary.is_file() and digest(binary) == value['binary_sha256']:
                save(logs / 'elf2tab-cache.json', value); return binary
        if cache.exists(): shutil.rmtree(cache)
        cache.mkdir()
        env = dependencies.environment(root, cache / 'cargo-home')
        env['CARGO_TARGET_DIR'] = str(cache / 'target')
        result = run_logged(['cargo', 'build', '--locked', '--offline', '--release', '--manifest-path',
                             str(root / 'third_party/hardware_tools/elf2tab/Cargo.toml')], root,
                            logs / 'elf2tab-build.log', timeout=300, environment=env)
        if result['status'] != 'pass': raise RuntimeError('elf2tab build failed: ' + str(result))
        shutil.copy2(cache / 'target/release/elf2tab', binary)
        value = {'source_manifest': evidence['manifest_sha256'], 'binary_sha256': digest(binary),
                 'version': '0.12.0', 'build': result}
        save(receipt, value); save(logs / 'elf2tab-cache.json', value)
        shutil.rmtree(cache / 'target'); shutil.rmtree(cache / 'cargo-home')
        return binary

