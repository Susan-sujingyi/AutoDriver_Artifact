"""Native float-unit bounds from DHT20 V1.0 May2021 and existing independent frames.

The old Tock numeric dataset is shared evidence, not independent new devices.
Reference/source drivers and method IR are never imported.
"""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import runpy
HERE=Path(__file__).resolve().parent
ROOT=Path(__file__).resolve().parents[3]
BASIS=Path(__file__).resolve().parent

def render():
    oracle=runpy.run_path(str(BASIS/'oracle.py'))
    oracle['validate_vectors']()
    rows=[]; records=[]
    for case in json.loads((BASIS/'cases.yaml').read_text())['vectors']:
        raw=case['raw']; frame=oracle['encode_sample'](raw,case['status'])
        assert frame==case['raw_bytes']
        exact=[Fraction(200*raw['temperature'],1048576)-50,
               Fraction(100*raw['humidity'],1048576)]
        # Existing centi-unit arithmetic remains admissible; float rounding
        # is included. Sensor accuracy is not the deterministic tolerance.
        low=[float(x)-.011 for x in exact]; high=[float(x)+.011 for x in exact]
        low[1]=max(0,low[1]);high[1]=min(100,high[1])
        rows.append('{'+json.dumps(case['id'])+', {'+','.join(map(str,frame))+'}, {'+
                    ','.join(map(repr,low))+'}, {'+','.join(map(repr,high))+'}}')
        records.append({'id':case['id'],'frame':frame,'exact':[str(x) for x in exact],
                        'low':low,'high':high})
    header=('/* Generated from independent rational conversions and CRC witnesses. */\n'
            'struct vector {const char *name; uint8_t frame[7]; double low[2],high[2];};\n'
            'static const struct vector vectors[]={\n'+',\n'.join(rows)+'\n};\n')
    evidence={'schema':'driverbench.native-dht20-vectors/v1',
              'shared_dataset':'benchmark/evaluator_v2/dht20; not independent of Tock vectors',
              'inputs':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in (BASIS/'oracle.py',BASIS/'cases.yaml',ROOT/'benchmark/dht20/documents/datasheet.pdf')},
              'records':records}
    return header,evidence

if __name__=='__main__':
    header,evidence=render()
    (HERE/'vectors.h').write_text(header)
    (HERE/'vectors.json').write_text(json.dumps(evidence,indent=2)+'\n')
