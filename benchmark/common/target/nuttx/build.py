"""Native NuttX build of a validated candidate; source compilation is separate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from benchmark.common.api import ROOT, copy_tree, digest, identity, inventory, save
from tests.common.sandbox import run, tool_roots
from .command import verify_command
from .boot import install_boot, verify_boot
from .wave import install_wave
from benchmark.common.release import validation_identity

def build_target(task, candidate, support, out, candidate_identity):
    root = ROOT
    task_id = task.id
    spec = {"device_key": task.key, "bus": task.bus}
    out.mkdir(parents=True, exist_ok=False)
    tree = out / "workspace"
    tree.mkdir()
    copy_tree(candidate, tree / "nuttx")
    copy_tree(support / "apps", tree / "apps")
    install_boot(tree)
    wave_integration = install_wave(tree) if task.key == "dht11" else None
    identity_data = {"task_id": task_id, "candidate_identity": candidate_identity,
                     "support_identity": validation_identity(task),
                     "task_identity": identity(task.files()), "adapter_sha256": digest(Path(__file__))}
    build_id = identity(identity_data)
    header = tree / "apps/system/sensor_sample/run_identity.h"
    header.write_text('#define SENSOR_SAMPLE_TASK_ID "' + task_id + '"\n'
                      '#define SENSOR_SAMPLE_BUILD_ID "' + build_id + '"\n')
    from benchmark.common.target.naming import check_changes
    check_changes(tree / "nuttx", inventory(candidate), inventory(tree / "nuttx"))
    check_changes(tree / "apps", inventory(support / "apps"), inventory(tree / "apps"))
    for name in ('tmp', 'home'):
        (tree / name).mkdir()
    env = {'PATH': os.pathsep.join([str(root / 'third_party/tools/kconfig-frontends/usr/bin'),
                                    str(root / '.venv/bin'), '/usr/bin', '/bin']),
           'HOME': str(tree / 'home'), 'TMPDIR': str(tree / 'tmp'),
           'PYTHON': str(root / '.venv/bin/python'), 'PYTHONDONTWRITEBYTECODE': '1',
           'LC_ALL': 'C', 'LANG': 'C', 'SOURCE_DATE_EPOCH': '0',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
    from benchmark.common.target.commands import nuttx_configuration
    config, disabled, steps = nuttx_configuration(task.bus, task.sensor)
    report = {'schema': 'driverbench.cross-os-native-build/v1', **identity_data,
              'build_id': build_id, 'status': 'running', 'steps': [],
              'boundary': 'landlock+seccomp', 'model_calls': 0, 'hardware_operations': 0,
              'behavior_validation': 'separate gate', 'physical_validation': 'not_run',
              'compiler': subprocess.check_output(['arm-none-eabi-gcc', '--version'], text=True).splitlines()[0]}
    if wave_integration is not None:
        report['wave_integration'] = wave_integration
    save(out / 'report.json', report)
    for i, command in enumerate(steps):
        log = out / ('step-' + str(i) + '.log')
        execution = run(command, tree / 'nuttx', log, readonly=tool_roots(root),
                        writable=[tree], environment=env)
        report['steps'].append({'command': command, **execution, 'log_sha256': digest(log)})
        if execution['status'] != 'pass':
            report['status'] = 'not_completed' if execution['timed_out'] else 'compile_failed'
            break
    else:
        normalized = (tree / 'nuttx/.config').read_text()
        missing = [key for key in config if 'CONFIG_' + key + '=y\n' not in normalized]
        incompatible = [key for key in disabled
                        if 'CONFIG_' + key + '=y\n' in normalized]
        if spec['bus'] == 'gpio' and 'CONFIG_USEC_PER_TICK=10000\n' not in normalized:
            missing.append('USEC_PER_TICK=10000')
        if spec['device_key'] == 'isl29035' and 'CONFIG_USEC_PER_TICK=1000\n' not in normalized:
            missing.append('USEC_PER_TICK=1000')
        if missing or incompatible:
            report['status'] = 'compile_failed'
            report['configuration_error'] = {'missing': missing, 'conflicting': incompatible}
        else:
            package = out / 'firmware'
            package.mkdir()
            for source, destination in [('nuttx', 'kernel.elf'), ('nuttx.bin', 'kernel.bin'),
                                        ('.config', 'nuttx.config')]:
                shutil.copyfile(tree / 'nuttx' / source, package / destination)
            report['artifacts'] = {p.name: digest(p) for p in package.iterdir()}
            try:
                report['command_registration'] = verify_command(
                    package / 'kernel.elf', package / 'kernel.bin', package / 'nuttx.config',
                    task_id=task_id, build_id=build_id)
                report['automatic_startup'] = verify_boot(package / 'kernel.elf', package / 'nuttx.config')
                report['status'] = 'pass'
            except ValueError as error:
                report['status'] = 'compile_failed'
                report['command_registration'] = {'status': 'compile_failed', 'error': str(error)}
    save(out / 'report.json', report)
    shutil.rmtree(tree)
    return report
