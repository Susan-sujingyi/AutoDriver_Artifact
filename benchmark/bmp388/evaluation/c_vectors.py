"""Render native numeric witnesses from the inherited independent rational oracle."""
from pathlib import Path
import hashlib
import json
import runpy

ROOT=Path(__file__).resolve().parents[3]
BASE=Path(__file__).resolve().parent


def render():
    oracle=runpy.run_path(str(BASE/'oracle.py'))
    cases=json.loads((BASE/'cases.yaml').read_text())
    lines=['/* Generated from independent exact Bosch-polynomial witnesses. */',
           'struct vector {const char *id;uint8_t cal[21],raw[6];long double expected[2];};',
           'static const struct vector vectors[]={']
    for v in cases['vectors']:
        cal=cases['calibration_profiles'][v['profile']]
        result=oracle['compensate'](cal['coefficients'],v['raw'])
        values=[result['temperature_c'],result['pressure_pa']/100]
        expected=','.join(f'({n.numerator}.0L/{n.denominator}.0L)' for n in values)
        lines.append('{'+json.dumps(v['id'])+',{'+','.join(map(str,cal['bytes']))+'},{'+
                     ','.join(map(str,v['raw_bytes']))+'},{'+expected+'}},')
    lines+=['};','#define VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))']
    return '\n'.join(lines)+'\n'


if __name__=='__main__':
    out=Path(__file__).with_name('vectors.h');text=render()
    if out.exists() and out.read_text()!=text:raise ValueError('Existing vector bytes differ; review a new revision')
    out.write_text(text)
    print(json.dumps({'vectors_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
        'basis':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (BASE/'oracle.py',BASE/'cases.yaml')}}))
