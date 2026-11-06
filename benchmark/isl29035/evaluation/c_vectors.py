"""Independent native float bounds from the original ISL29035 numeric oracle.

All 256 selected ADC counts are covered. Existing named vectors are shared
with Tock; they are not a new independent sensor dataset.
"""
from pathlib import Path
from fractions import Fraction
import hashlib,json,runpy
HERE=Path(__file__).resolve().parent
ROOT=Path(__file__).resolve().parents[3]
BASIS=Path(__file__).resolve().parent
def render():
    oracle=runpy.run_path(str(BASIS/'oracle.py'));oracle['validate_vectors']()
    records=[];rows=[]
    for raw in range(256):
        exact=oracle['decode_lux'](raw)
        assert exact==Fraction(125*raw,8)
        low=max(0,int(exact)-1);high=int(exact)+1
        rows.append('{'+f'{raw}, {low}.0, {high}.0'+'}')
        records.append({'raw':raw,'exact_lux':str(exact),'low':low,'high':high})
    header=('/* Generated from independent rational basis, not driver output. */\n'
            'struct vector {uint8_t raw;double low,high;};\n'
            'static const struct vector vectors[]={\n'+',\n'.join(rows)+'\n};\n')
    evidence={'schema':'driverbench.native-isl29035-vectors/v1',
              'shared_dataset':'Original ISL29035 rational oracle; all selected codes, not independent of Tock numeric basis.',
              'tolerance':'Inherited whole-lux truncation +/-1 lux; native float result also allowed within these bounds.',
              'inputs':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                        (BASIS/'oracle.py',BASIS/'cases.yaml',ROOT/'benchmark/isl29035/documents/datasheet.pdf')},
              'records':records}
    return header,evidence
if __name__=='__main__':
    header,evidence=render();(HERE/'vectors.h').write_text(header)
    (HERE/'vectors.json').write_text(json.dumps(evidence,indent=2)+'\n')
