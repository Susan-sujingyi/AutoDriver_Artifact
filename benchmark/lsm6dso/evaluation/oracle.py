"""Independent LSM6DSO±2g nominal SI oracle, ST DS12140 Rev3 Table2.

Sensitivity0.061mg/LSB is used exactly as a decimal table value. It is not
1/16384g/LSB (MPU6050), and it does not certify physical calibration accuracy.
No source driver, method IR or generated code is imported.
"""
from fractions import Fraction
from decimal import Decimal
from pathlib import Path
import json

def ideal_axis(count):
    if type(count) is not int or not -32768<=count<=32767:raise ValueError('signed16 count required')
    return Fraction(count*61*9806650,1000000)

def decode_sample(raw_bytes):
    if len(raw_bytes)!=6 or any(type(b) is not int or not 0<=b<=255 for b in raw_bytes):raise ValueError('six unsigned acceleration bytes required')
    b=bytes(raw_bytes)
    return {'acceleration_xyz_um_s2':[int(ideal_axis(int.from_bytes(b[i:i+2],'little',signed=True))) for i in (0,2,4)]}

def encode_sample(raw):
    b=[]
    for axis in ('x','y','z'):ideal_axis(raw[axis]);b.extend(raw[axis].to_bytes(2,'little',signed=True))
    return b

def validate_vectors(path=None):
    cases=json.loads((Path(path) if path else Path(__file__).with_name('cases.yaml')).read_text())
    for v in cases['vectors']:
        assert encode_sample(v['raw'])==v['raw_bytes'],v['id']
        assert decode_sample(v['raw_bytes'])==v['expected'],v['id']
        assert [ideal_axis(v['raw'][axis]) for axis in ('x','y','z')]==[Fraction(x) for x in v['ideal_xyz_fraction']],v['id']
        assert [x&0xffffffff for x in v['expected']['acceleration_xyz_um_s2']]==v['hil_words'],v['id']
    for v in cases['invalid_vectors']:
        try:decode_sample(v['raw_bytes'])
        except ValueError:pass
        else:raise AssertionError(v['id'])
    for bits in range(65536):
        lo=bits%256;hi=bits//256;count=lo+(hi-256 if hi>=128 else hi)*256
        value=int(Decimal(count)*Decimal('0.061')*Decimal('9.80665')*Decimal(1000))
        assert decode_sample([lo,hi]*3)['acceleration_xyz_um_s2']==[value]*3,bits
        word=value&0xffffffff;decoded=word-4294967296 if word&0x80000000 else word;assert decoded==value
    return {'vectors':len(cases['vectors']),'invalid_vectors':len(cases['invalid_vectors']),'exhaustive_axis_encodings':65536,'axes_checked_per_encoding':3}


def render_vectors(directory):
 import json
 from math import ceil,floor
 data=json.loads((directory/'cases.yaml').read_text());lines=[];records=[]
 for v in data['vectors']:
  q=[ideal_axis(v['raw'][a])for a in ['x','y','z']];lower=[ceil(x-1)for x in q];upper=[floor(x+1)for x in q]
  actual=decode_sample(v['raw_bytes']);assert actual==v['expected']
  lines.append('Vector{id:'+json.dumps(v['id'])+',raw:'+str(v['raw_bytes'])+',expected:'+str(actual['acceleration_xyz_um_s2'])+',lower:'+str(lower)+',upper:'+str(upper)+'}')
  records.append({**v,'independently_computed':actual,'lower':lower,'upper':upper})
 return 'const VECTORS:&[Vector]=&[\n'+',\n'.join(lines)+'\n];\n',records
