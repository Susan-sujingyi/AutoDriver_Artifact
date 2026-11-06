"""Isolated host execution of method-owned NuttX public-ABI plans."""
from pathlib import Path
import shutil
import time

from benchmark.common.api import ROOT,save,digest
from tests.common.autodriver_json import load
from method.autodriver.implementation.system.contract.model import parse,Unknown
from method.autodriver.implementation.system.protocol.assembler import digest as sha
from method.autodriver.implementation.system.native.nuttx import native_plan,evaluate
from tests.common.autodriver_candidate import host_path,tool_environment
from tests.common.execution.process import execute
from tests.common.sandbox import tool_roots


def native_nuttx(candidate,destination,state,*,deadline=None,framework_only=False):
    candidate=host_path(candidate);destination=host_path(destination)
    if destination.exists():raise ValueError('native NuttX destination already exists')
    destination.mkdir(parents=True);task=load(candidate/'task.yaml')
    def remaining(limit):
        return limit if deadline is None else max(0.01,min(limit,deadline-time.monotonic()))
    if deadline is not None and time.monotonic()>=deadline:
        result={'status':'not_completed','reason':'shared trial deadline reached','native_candidate_executed':False}
        save(destination/'native-report.json',result);return result
    if state['task_id']!=task['task_id']:raise ValueError('native contracts belong to a different task')
    materials={item['id']:item for item in state['materials']}
    def contract(key,kind):
        record=materials[state['objects'][key]['material_id']];text=record['text']
        if sha(text.encode())!=record['sha256']:raise ValueError('native contract state changed')
        return parse(text,kind)
    if framework_only:
        from method.autodriver.implementation.system.native.framework_probe import profile,nuttx_plan,evaluate as probe_check
        dbc=None;fp=profile(state);bp=None
    else:
        dbc=contract('dbc','dbc');fp=contract('target_fp','fp');bp=contract('bp','bp') if 'bp' in state['objects'] else None
    try:plan=nuttx_plan(task,candidate) if framework_only else native_plan(task,dbc,candidate)
    except Unknown as error:
        result={'status':'unknown','reason':str(error),'native_candidate_executed':False};save(destination/'native-report.json',result);return result
    stage=destination/'staging';stage.mkdir();include=stage/'include'
    for relative,text in plan['headers'].items():
        path=include/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    source=stage/'main.c';source.write_text(plan['source']);binary=stage/'native-candidate'
    module=task['target']['module'];driver=candidate/('target/drivers/sensors/'+module+'.c')
    environment,_=tool_environment(destination/'tool-state');roots=tool_roots(ROOT)+[candidate,destination]
    command=['/usr/bin/gcc','-std=gnu11','-D_DEFAULT_SOURCE','-O0','-g','-I',str(include),str(source),str(driver),'-lm','-o',str(binary)]
    build=execute(command,cwd=stage,readonly=roots,writable=[destination,Path('/dev/null')],environment=environment,
        output=destination/'build-process',timeout=remaining(120),gateway_port=None)
    result={'schema':'autodriver.native-run/v1','producer':'method_trace_checker','build':build,'plan':{key:value for key,value in plan.items() if key not in {'source','headers'}},
        'source_hashes':{'main':digest(source),'driver':digest(driver)},'native_candidate_executed':False,
        'hardware_status':'not_run','independent_scoring':'not_run','status':'native_adapter_build_failed'}
    if build['status']=='pass':
        results=[];chunks=[]
        for index,case in enumerate(plan['cases']):
            if deadline is not None and time.monotonic()>=deadline:break
            output=destination/('case-'+str(index))
            run=execute([str(binary),str(index)],cwd=stage,readonly=tool_roots(ROOT)+[binary],writable=[Path('/dev/null')],
                environment={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8'},output=output,timeout=remaining(10),gateway_port=None)
            results.append(dict(case=case['case'],**run));chunks.append((output/'stdout.jsonl').read_text())
            if run['status']!='pass':break
        combined=destination/'run-process';combined.mkdir();(combined/'stdout.jsonl').write_text(''.join(chunks))
        result.update(executions=results,native_candidate_executed=True,status='native_execution_failed')
        if len(results)==len(plan['cases']) and all(run['status']=='pass' for run in results):
            checks=probe_check(dbc,fp,plan,''.join(chunks)) if framework_only else evaluate(dbc,fp,bp,plan,''.join(chunks))
            result.update(status='observed',checks=checks)
    save(destination/'native-report.json',result);return result
