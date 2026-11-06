"""Render original independent magnetic sensitivity witnesses in native uT."""
from pathlib import Path
import json
import runpy

ROOT=Path(__file__).resolve().parents[3]
BASE=Path(__file__).resolve().parent


def render():
    oracle=runpy.run_path(str(BASE/'oracle.py'))
    records=json.loads((BASE/'cases.yaml').read_text())['vectors']
    rows=['/* Exact ST Table75 rational witnesses; independently generated. */',
          'struct vector {const char *id;int16_t xyz[3];uint8_t wire[6];long double ut[3];};',
          'static const struct vector vectors[]={']
    for record in records:
        xyz=record['xyz_counts'];values=[v/1000 for v in oracle['ideal'](xyz)]
        rows.append('{'+json.dumps(record['id'])+',{'+','.join(map(str,xyz))+'},{'+
                    ','.join(map(str,oracle['wire'](xyz)))+'},{'+
                    ','.join(f'({v.numerator}.0L/{v.denominator}.0L)' for v in values)+'}},')
    return '\n'.join(rows+['};','#define VECTOR_COUNT (sizeof(vectors)/sizeof(vectors[0]))'])+'\n'


if __name__=='__main__':
    out=Path(__file__).with_name('vectors.h');text=render()
    if out.exists() and out.read_text()!=text:raise ValueError('Existing vector bytes differ; review a new revision')
    out.write_text(text)
