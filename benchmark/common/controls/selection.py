"""Explicit benchmark control plans, plus native NuttX fixture construction."""
import runpy
from benchmark.common.api import ROOT, owned, load
from .nuttx import control_source


def plan(task):
    catalog=load(ROOT/f'benchmark/{task.sensor}/evaluation/controls/catalog.yaml')
    profile=catalog.get('profiles',{}).get(task.os+'/'+task.bus)
    if not profile or not profile.get('controls'):
        raise ValueError('No explicit control plan: '+task.id)
    entries=profile['controls']
    if len({e['name'] for e in entries})!=len(entries) or not any(e['expected']=='pass' for e in entries):
        raise ValueError('Control plan must name unique fixtures and a legal alternative')
    faults=[e for e in entries if e['expected']=='fail']
    if not faults or any(not e.get('required_failure_cases') for e in faults):
        raise ValueError('Every defect needs a declared failing contract case family')
    return entries


def variants(task):
    if task.os!='nuttx':raise ValueError('Native NuttX control selection only')
    text=owned(next(iter(task.spec['reference']['files'].values()))).read_text()
    result=[]
    for entry in plan(task):
        if entry['generator']=='native_api_variant':
            source=control_source(task,text,entry['name'])
        elif entry['generator']=='sensor_mutation':
            path=ROOT/f'benchmark/{task.sensor}/evaluation/controls/nuttx.py'
            before,after=runpy.run_path(str(path))['mutations']()[entry['name']]
            if text.count(before)!=1:
                raise ValueError('Selected mutation anchor must match exactly once: '+task.id+'/'+entry['name'])
            source=text.replace(before,after,1)
        else:
            raise ValueError('Unapproved control generator')
        if source==text:raise ValueError('Selected control leaves reference unchanged')
        result.append((entry['name'],source,entry['expected']))
    return result
