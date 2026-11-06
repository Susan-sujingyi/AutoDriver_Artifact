"""Host candidate preparation and public compilation; no scoring imports."""
from pathlib import Path
import os
import shutil
import time

from benchmark.common.api import ROOT,save,resolve_task,inventory
from tests.common.autodriver_json import load
from benchmark.common.target.prepare import verify_baseline
from method.autodriver.implementation.system.code import apply_edits
from tests.common.sandbox import tool_roots
from tests.common.execution.process import execute


def host_path(path):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT/'run') or 'host' not in path.relative_to(ROOT/'run').parts:
        raise ValueError('candidate mutation is restricted to prepared run/host copies')
    return path


def prepare_candidate(agent,baseline,destination,proposal):
    agent=Path(agent).resolve(strict=True);baseline=host_path(baseline);destination=host_path(destination)
    if destination.exists():raise ValueError('candidate copy already exists')
    if proposal.get('applied') is not False:raise ValueError('unapplied method proposal required')
    task=load(agent/'task.yaml');record=verify_baseline(resolve_task(task['task_id']),baseline)
    if record['support_files']!=inventory(agent/'support'):
        raise ValueError('candidate support differs from prepared baseline')
    started=time.time();destination.mkdir(parents=True,exist_ok=False)
    shutil.copytree(baseline/'target',destination/'target',symlinks=True)
    shutil.copytree(agent/'support',destination/'support',symlinks=True)
    shutil.copyfile(agent/'task.yaml',destination/'task.yaml');(destination/'output').mkdir()
    changes=apply_edits(destination,proposal['artifact'],proposal['request'])
    from benchmark.common.submission import export_patch
    submission=export_patch(resolve_task(task['task_id']),baseline,destination/'target',destination/'output/submission')
    record={'schema':'autodriver.development-candidate/v1','task_id':task['task_id'],'baseline_identity':record['baseline_identity'],
        'revision':proposal['revision'],'full_scope_gate':proposal['full_scope_gate'],
        'generation_scope':proposal['generation_scope'],'changes':changes,'submission':submission,'started':started,'finished':time.time(),
        'scoring_status':'not_run','hardware_status':'not_run','classification':'development_only'}
    save(destination/'candidate.json',record)
    return record


def tool_environment(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    for name in ('home','tmp'): (output/name).mkdir(exist_ok=True)
    rust=Path(os.environ.get('RUSTUP_HOME',str(Path.home()/'.rustup'))).resolve(strict=True)
    cargo=Path.home()/'.cargo/bin'
    environment={'PATH':os.pathsep.join([str(ROOT/'.venv/bin'),str(cargo),str(ROOT/'third_party/tools/kconfig-frontends/usr/bin'),'/usr/bin','/bin']),
        'HOME':str(output/'home'),'TMPDIR':str(output/'tmp'),'PYTHONDONTWRITEBYTECODE':'1','LC_ALL':'C.UTF-8','LANG':'C.UTF-8',
        'PYTHON':str(ROOT/'.venv/bin/python'),'RUSTUP_HOME':str(rust),'RUSTUP_TOOLCHAIN':'nightly-2024-01-01',
        'AUTODRIVER_RUST_VENDOR':str(ROOT/'third_party/rust_vendor/sources'),
        'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'}
    return environment,[cargo,rust/'toolchains',rust/'settings.toml']


def public_compile(candidate,destination,*,timeout=600):
    candidate=host_path(candidate);destination=host_path(destination)
    environment,rust=tool_environment(candidate/'output/tool-state')
    scratch=candidate/'output/public-compile'
    command=[str(ROOT/'.venv/bin/python'),str(candidate/'support/build.py'),'--workspace',str(candidate),'--output',str(scratch)]
    result=execute(command,cwd=candidate,readonly=tool_roots(ROOT)+[candidate,ROOT/'third_party/rust_vendor']+[p for p in rust if p.exists()],
        writable=[candidate/'output',Path('/dev/null')],environment=environment,output=destination,
        timeout=timeout,gateway_port=None)
    reports=load(scratch/'report.json') if (scratch/'report.json').is_file() else []
    value={'schema':'autodriver.public-compile/v1','execution':result,'commands':reports,
        'status':'pass' if result['status']=='pass' and reports and all(item['returncode']==0 for item in reports) else 'not_completed' if result.get('timed_out') else 'fail',
        'producer':'approved_compile_tool','scope':'public native build only; no device behavior or hardware validation'}
    save(destination/'public-compile.json',value)
    return value
