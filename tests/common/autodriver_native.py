"""Host runner for method-owned plans, with candidate execution denied network.

Only public prepared target code and the current method contracts are inputs.
No independent scoring/reference module is imported or invoked.
"""
import json
from pathlib import Path
import shutil
import tomllib
import time

from benchmark.common.api import ROOT,save,digest
from tests.common.autodriver_json import load
from method.autodriver.implementation.system.contract.model import parse,Unknown
from method.autodriver.implementation.system.native.plan import tock_plan,evaluate
from tests.common.autodriver_candidate import host_path,tool_environment
from tests.common.execution.process import execute
from tests.common.sandbox import tool_roots


def native_tock(candidate,destination,state,*,deadline=None,framework_only=False):
    candidate=host_path(candidate);destination=host_path(destination)
    if destination.exists():raise ValueError('native execution destination already exists')
    destination.mkdir(parents=True,exist_ok=False)
    def remaining(limit):
        return limit if deadline is None else max(0.01,min(limit,deadline-time.monotonic()))
    if deadline is not None and time.monotonic()>=deadline:
        result={'status':'not_completed','reason':'shared trial deadline reached','native_candidate_executed':False}
        save(destination/'native-report.json',result);return result
    task=load(candidate/'task.yaml');materials={m['id']:m for m in state['materials']}
    if state['task_id']!=task['task_id']:raise ValueError('native contracts belong to a different task')
    def contract(key,kind):
        item=state['objects'][key];data=materials[item['material_id']]['text']
        from method.autodriver.implementation.system.protocol.assembler import digest as sha
        if sha(data.encode())!=materials[item['material_id']]['sha256']:raise ValueError('native contract state hash changed')
        return parse(data,kind)
    if framework_only:
        from method.autodriver.implementation.system.native.framework_probe import profile, tock_plan as probe_plan, evaluate as probe_check
        dbc=None;fp=profile(state);bp=None
    else:
        dbc=contract('dbc','dbc');fp=contract('target_fp','fp');bp=contract('bp','bp') if 'bp' in state['objects'] else None
    spi=bool(task['requirements']['connection'].get('spi'))
    if spi:
        from method.autodriver.implementation.system.native.spi import native_plan as make_plan,evaluate as check_native
    else:make_plan,check_native=tock_plan,evaluate
    if framework_only:check_native=probe_check
    try:plan=probe_plan(task,candidate) if framework_only else make_plan(task,dbc,candidate,bp,fp)
    except Unknown as error:
        result={'status':'unknown','reason':str(error),'native_candidate_executed':False}
        save(destination/'native-report.json',result);return result
    stage=destination/'crate';(stage/'src').mkdir(parents=True)
    (stage/'src/main.rs').write_text(plan['source']);(stage/'src/observer.rs').write_text(plan['observer'])
    for name,text in plan.get('auxiliary_sources',{}).items():
        if Path(name).name!=name:raise ValueError('invalid native auxiliary source name')
        (stage/'src'/name).write_text(text)
    dependency_manifest=candidate/'target/capsules/extra/Cargo.toml'
    dependencies=tomllib.loads(dependency_manifest.read_text())['dependencies'];rows=[]
    for name,description in dependencies.items():
        if not isinstance(description,dict) or 'path' not in description:
            raise Unknown('host native dependency adapter requires public local path dependencies')
        path=(dependency_manifest.parent/description['path']).resolve(strict=True)
        if not path.is_relative_to(candidate/'target'):raise ValueError('native dependency escapes prepared public target')
        rows.append(json.dumps(name)+'={path='+json.dumps(str(path))+'}')
    (stage/'Cargo.toml').write_text('[package]\nname="autodriver-native-candidate"\nversion="0.0.0"\nedition="2021"\n[dependencies]\n'+'\n'.join(rows)+'\n')
    environment,rust=tool_environment(destination/'tool-state')
    cargo=stage/'cargo';cargo.mkdir()
    (cargo/'config.toml').write_text('[net]\noffline=true\n[source.crates-io]\nreplace-with="bundled"\n[source.bundled]\ndirectory='+json.dumps(environment['AUTODRIVER_RUST_VENDOR'])+'\n')
    environment.update(CARGO_HOME=str(cargo),CARGO_INCREMENTAL='0',CARGO_NET_OFFLINE='true')
    roots=tool_roots(ROOT)+[candidate,destination,ROOT/'third_party/rust_vendor']+[p for p in rust if p.exists()]
    build=execute([str(Path.home()/'.cargo/bin/cargo'),'build','--offline','--manifest-path',str(stage/'Cargo.toml')],
        cwd=stage,readonly=roots,writable=[destination,Path('/dev/null')],environment=environment,
        output=destination/'build-process',timeout=remaining(120),gateway_port=None)
    result={'schema':'autodriver.native-run/v1','producer':'method_trace_checker','build':build,'plan':{k:v for k,v in plan.items() if k not in {'source','observer','auxiliary_sources'}},
        'source_hashes':{'main':digest(stage/'src/main.rs'),'observer':digest(stage/'src/observer.rs'),'public_dependency_manifest':digest(dependency_manifest)},
        'native_candidate_executed':False,'hardware_status':'not_run','independent_scoring':'not_run','status':'build_failed'}
    result['source_hashes']['candidate_module']=digest((candidate/'target/capsules/extra/src'/task['target']['module']).with_suffix('.rs'))
    result['source_hashes']['public_sensor_hil']=digest(candidate/'target/kernel/src/hil/sensors.rs')
    result['source_hashes']['public_time_hil']=digest(candidate/'target/kernel/src/hil/time.rs')
    result['source_hashes']['public_bus_hil']=digest(candidate/('target/kernel/src/hil/spi.rs' if spi else 'target/kernel/src/hil/i2c.rs'))
    result['source_hashes']['auxiliary']={name:digest(stage/'src'/name) for name in plan.get('auxiliary_sources',{})}
    result['contract_bindings']={key:{'material_id':state['objects'][key]['material_id'],
        'sha256':materials[state['objects'][key]['material_id']]['sha256']} for key in ('dbc','target_fp','bp') if key in state['objects']}
    if build['status']=='pass' and (deadline is None or time.monotonic()<deadline):
        binary=stage/'target/debug/autodriver-native-candidate'
        run=execute([str(binary)],cwd=stage,readonly=tool_roots(ROOT)+[binary],writable=[Path('/dev/null')],
            environment={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},output=destination/'run-process',timeout=remaining(10),gateway_port=None)
        result.update(execution=run,native_candidate_executed=True,status='execution_failed')
        if run['status']=='pass':
            stdout=(destination/'run-process/stdout.jsonl').read_text()
            # execute retains plain stdout despite the historical .jsonl name.
            result.update(status='observed',checks=check_native(dbc,fp,plan,stdout,bp))
            if not framework_only and not spi:
                from method.autodriver.implementation.system.native.framework_probe import evaluate as framework_checks
                observed=framework_checks(None,fp,plan,stdout)
                result['checks']['framework_checks']=observed
                if observed['status']=='violated':result['checks']['status']='violated'
    save(destination/'native-report.json',result);return result
