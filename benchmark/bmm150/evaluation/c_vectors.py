"""Render independent exact rational Bosch magnetic compensation witnesses."""
from pathlib import Path
from fractions import Fraction
import json
import random
import runpy

ROOT=Path(__file__).resolve().parents[3]
BASE=Path(__file__).resolve().parent


def records():
    oracle=runpy.run_path(str(BASE/'oracle.py'))
    result=[]
    original=json.loads((BASE/'cases.yaml').read_text())['vectors']
    for v in original:
        expected=oracle['compensate'](oracle['decode_trim'](v['trim_bytes']),oracle['decode_sample'](v['bytes']))
        result.append({**v,'exact':expected['exact_nanotesla']})
    # Calibration/raw combinations are synthetic arithmetic witnesses, not
    # hardware factory-trim measurements. Keep the21 inherited vectors first.
    rng=random.Random(150)
    for v in original:
        trim=oracle['decode_trim'](v['trim_bytes'])
        for j in range(16):
            raw={'x':rng.randrange(-4095,4096),'y':rng.randrange(-4095,4096),
                 'z':rng.randrange(-16383,16384),'hall':rng.randrange(1,16384)}
            try:expected=oracle['compensate'](trim,raw)
            except ValueError:continue
            result.append({'id':v['id']+'_mixed_'+str(j),'trim_bytes':v['trim_bytes'],
                           'bytes':oracle['encode_sample'](raw),'exact':expected['exact_nanotesla']})
    return result


def render():
    rows=['/* Exact rational Bosch compensation witnesses, native microtesla. */',
          'struct vector {const char *id;uint8_t trim[21],wire[8];long double ut[3];};',
          'static const struct vector vectors[]={']
    for v in records():
        values=[Fraction(x['numerator'],1000*x['denominator']) for x in v['exact']]
        rows.append('{'+json.dumps(v['id'])+',{'+','.join(map(str,v['trim_bytes']))+'},{'+
                    ','.join(map(str,v['bytes']))+'},{'+
                    ','.join(f'({x.numerator}.0L/{x.denominator}.0L)' for x in values)+'}},')
    return '\n'.join(rows+['};','#define VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))'])+'\n'


if __name__=='__main__':
    out=Path(__file__).with_name('vectors.h');text=render()
    if out.exists() and out.read_text()!=text:raise ValueError('Existing vector bytes differ; review a new revision')
    out.write_text(text)
