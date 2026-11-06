"""Native units from the existing independent rational LPS22HB basis.

This reuses Tock's rated samples; it is not an additional independent dataset.
"""
from pathlib import Path
from fractions import Fraction
from math import floor, ceil
import hashlib, json, runpy
HERE=Path(__file__).resolve().parent
ROOT=Path(__file__).resolve().parents[3]
BASIS=Path(__file__).resolve().parent
def render():
    oracle=runpy.run_path(str(BASIS/'oracle.py'));oracle['validate_vectors']()
    records=[];rows=[]
    for v in json.loads((BASIS/'cases.yaml').read_text())['vectors']:
        assert v['within_rated_range']
        pressure=oracle['decode_pressure'](v['raw_bytes'][:3])
        temperature=Fraction(oracle['decode_temperature'](v['raw_bytes'][3:]),100)
        # Only float representation slack for the exact inherited centi-C.
        t=float(temperature);epsilon=max(abs(t)*2**-23,2**-23)
        low=[t-epsilon,max(0,floor(pressure)-1)]
        high=[t+epsilon,ceil(pressure)+1]
        rows.append('{'+json.dumps(v['id'])+',{'+','.join(map(str,v['raw_bytes']))+'},{'+','.join(map(repr,low))+'},{'+','.join(map(repr,high))+'}}')
        records.append({'id':v['id'],'bytes':v['raw_bytes'],'temperature_degc':str(temperature),
                        'pressure_hpa':str(pressure),'low':low,'high':high})
    header=('/* Generated from independent rational specification, not reference output. */\n'
            'struct vector {const char *name;uint8_t bytes[5];double low[2],high[2];};\n'
            'static const struct vector vectors[]={\n'+',\n'.join(rows)+'\n};\n')
    evidence={'schema':'driverbench.native-lps22hb-vectors/v1',
              'shared_dataset':'Original LPS22HB rated vectors and rational oracle; not independent of Tock numeric basis.',
              'tolerance':'Temperature: float representation only. Pressure: inherited floor-1..ceil+1 hPa, preserving integer-HIL tolerance.',
              'inputs':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                        (BASIS/'oracle.py',BASIS/'cases.yaml',ROOT/'benchmark/lps22hb/documents/datasheet.pdf')},
              'records':records}
    return header,evidence
if __name__=='__main__':
    header,evidence=render();(HERE/'vectors.h').write_text(header)
    (HERE/'vectors.json').write_text(json.dumps(evidence,indent=2)+'\n')
