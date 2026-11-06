"""Run only a public adapter process; host records are never method-readable."""
import json
import os
from pathlib import Path
from benchmark.common.api import ROOT, owned, save
from tests.common.sandbox import run, tool_roots


def run_method(name, agent, request, output, *, ledger_path=None, knowledge=None):
    if name != 'autodriver':raise ValueError('Unknown method')
    if request.get('operation') == 'run':
        if ledger_path is None:
            raise ValueError('Live execution requires an explicit shared host ledger')
        from tests.common.execution.runtime import run_live
        return run_live(name, agent, request, output, ledger_path=ledger_path, knowledge=knowledge)
    agent=Path(agent);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    request=dict(request,workspace=str(agent))
    # Requests and results live in output/, the explicit method handoff area.
    path=agent/'output/request.json';save(path,request)
    program=('import json,importlib,types,sys;from pathlib import Path;'
             'p=types.ModuleType("method");'
             f'p.__path__=[{str(ROOT / "method")!r}];sys.modules["method"]=p;'
             'p=types.ModuleType("method.common");'
             f'p.__path__=[{str(ROOT / "method/common")!r}];sys.modules["method.common"]=p;'
             f'p=types.ModuleType("method.{name}");'
             f'p.__path__=[{str(ROOT / "method" / name)!r}];sys.modules["method.{name}"]=p;'
             f'm=importlib.import_module("method.{name}.adapter");'
             'r=m.run(json.loads(Path("output/request.json").read_text()));'
             'Path("output/method-result.json").write_text(json.dumps(r))')
    args=[str(ROOT/'.venv/bin/python'),'-c',program]
    env={'PATH':str(ROOT/'.venv/bin')+':/usr/bin:/bin','PYTHONPATH':str(ROOT),
         'PYTHONDONTWRITEBYTECODE':'1','HOME':str(agent/'output'),'LC_ALL':'C'}
    record=run(args,agent,output/'adapter.log',readonly=tool_roots(ROOT)+[ROOT/'method/common', ROOT/'method'/name],writable=[agent],environment=env,timeout=60)
    if record['status']=='pass':record['result']=json.loads((agent/'output/method-result.json').read_text())
    save(output/'report.json',record);return record
