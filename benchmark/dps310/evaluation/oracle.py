"""Independent DPS310 arithmetic: Infineon V1.2 pp. 14–15, 37.

Exact rational polynomial; no method IR, source-driver arithmetic or target
reference is consulted. Synthetic coefficient profiles are not factory data.
"""
from fractions import Fraction
from decimal import Decimal, localcontext

LAYOUT = [('c0',12),('c1',12),('c00',20),('c10',20),
          ('c01',16),('c11',16),('c20',16),('c21',16),('c30',16)]
SCALE = 7864320


def word(value, width, signed=True):
    low, high = (-(1 << (width-1)), (1 << (width-1))-1) if signed else (0,(1<<width)-1)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Expected {width}-bit {"signed" if signed else "unsigned"} integer')
    return value


def decode_calibration(data):
    if len(data) != 18: raise ValueError('Expected eighteen bytes at 0x10..0x21')
    for b in data: word(b,8,False)
    # Interpret the datasheet table as one MSB-first field stream. This is
    # independent of the source driver's individual byte/shifting statements.
    packed = int.from_bytes(bytes(data),'big'); remaining = 144; result = {}
    for name,bits in LAYOUT:
        remaining -= bits; n=(packed>>remaining)&((1<<bits)-1)
        result[name]=n-(1<<bits) if n&(1<<(bits-1)) else n
    return result


def encode_calibration(coefficients):
    if set(coefficients)!=dict(LAYOUT).keys(): raise ValueError('Wrong coefficient fields')
    packed=0
    for name,bits in LAYOUT:
        n=word(coefficients[name],bits);packed=(packed<<bits)|(n&((1<<bits)-1))
    return list(packed.to_bytes(18,'big'))


def decode_sample(data):
    if len(data)!=6: raise ValueError('Expected six pressure/temperature bytes')
    for b in data:word(b,8,False)
    return {name:int.from_bytes(bytes(data[start:start+3]),'big',signed=True)
            for name,start in (('pressure',0),('temperature',3))}


def encode_sample(raw):
    if set(raw)!={'temperature','pressure'}:raise ValueError('Wrong raw channels')
    return [b for name in ('pressure','temperature')
            for b in word(raw[name],24).to_bytes(3,'big',signed=True)]


def compensate(c,raw):
    encode_calibration(c);encode_sample(raw)
    t=Fraction(raw['temperature'],SCALE);p=Fraction(raw['pressure'],SCALE)
    terms={'offset':Fraction(c['c00']),'linear_pressure':c['c10']*p,
           'quadratic_pressure':c['c20']*p*p,'cubic_pressure':c['c30']*p*p*p,
           'temperature':c['c01']*t,'linear_cross':c['c11']*t*p,
           'quadratic_cross':c['c21']*t*p*p}
    return {'scaled_temperature':t,'scaled_pressure':p,
            'temperature_c':Fraction(c['c0'],2)+c['c1']*t,
            'pressure_pa':sum(terms.values()),'pressure_terms':terms}


def convert(c,raw):
    v=compensate(c,raw)
    return {'temperature_centi_c':int(v['temperature_c']*100),
            'pressure_hpa':int(v['pressure_pa']/100)}


def decimal_crosscheck(c,raw):
    with localcontext() as ctx:
        ctx.prec=90;t=Decimal(raw['temperature'])/SCALE;p=Decimal(raw['pressure'])/SCALE
        temperature=Decimal(c['c0'])/2+Decimal(c['c1'])*t
        pressure=Decimal(c['c00'])+p*(c['c10']+p*(c['c20']+p*c['c30']))+t*(c['c01']+p*(c['c11']+p*c['c21']))
        return {'temperature_centi_c':int(temperature*100),'pressure_hpa':int(pressure/100)}


def exact_record(c,raw):
    def serialize(v):
        if isinstance(v,dict):return {k:serialize(x) for k,x in v.items()}
        return {'numerator':v.numerator,'denominator':v.denominator}
    return serialize(compensate(c,raw))


def validate_vectors():
    import json
    from pathlib import Path
    cases=json.loads(Path(__file__).with_name('cases.yaml').read_text())
    for vector in cases['vectors']+cases['oracle_only_vectors']:
        c=cases['calibration_profiles'][vector['profile']]['coefficients'];raw=vector['raw']
        assert decode_calibration(encode_calibration(c))==c
        assert decode_sample(encode_sample(raw))==raw
        assert convert(c,raw)==decimal_crosscheck(c,raw)==vector['expected']
        assert exact_record(c,raw)==vector['exact']
    return {'status':'pass','vectors':len(cases['vectors']),
            'oracle_only_vectors':len(cases['oracle_only_vectors'])}


def render_vectors(directory):
    import json
    from math import ceil, floor
    data=json.loads((directory/'cases.yaml').read_text());profiles=list(data['calibration_profiles']);lines=[];pairs=[];records=[]
    for v in data['vectors']:
        cal=data['calibration_profiles'][v['profile']];index=profiles.index(v['profile']);e=convert(cal['coefficients'],v['raw']);q=compensate(cal['coefficients'],v['raw'])
        physical=[q['temperature_c']*100,q['pressure_pa']/100];lower=[ceil(x-1) for x in physical];upper=[floor(x+1) for x in physical]
        lines.append('Vector{id:'+json.dumps(v['id'])+',profile:'+str(index)+',cal:'+str(cal['bytes'])+',raw:'+str([v['raw']['temperature'],v['raw']['pressure']])+',expected:'+str([e['temperature_centi_c'],e['pressure_hpa']])+',lower:'+str(lower)+',upper:'+str(upper)+'}')
        records.append({**v,'independently_computed':e,'exact_native':[str(x) for x in physical],'lower':lower,'upper':upper})
        for t in data['vectors']:
            if t['profile']!=v['profile']:continue
            raw={'temperature':t['raw']['temperature'],'pressure':v['raw']['pressure']};x=compensate(cal['coefficients'],raw)['pressure_pa']/100
            pairs.append('Pair{profile:'+str(index)+',t:'+str(raw['temperature'])+',p:'+str(raw['pressure'])+',expected:'+str(int(x))+',lower:'+str(ceil(x-1))+',upper:'+str(floor(x+1))+'}')
    return 'const VECTORS:&[Vector]=&[\n'+',\n'.join(lines)+'\n];\nconst PAIRS:&[Pair]=&[\n'+',\n'.join(pairs)+'\n];\n',records


if __name__ == '__main__':
    import json
    print(json.dumps(validate_vectors()))
