"""Independent exact BMP388 compensation, Bosch DS001-07 pp28,55-56.

No source driver, generated IR or reference capsule is consulted. Arithmetic
uses rational calibration coefficients and the manufacturer's polynomial,
not a translation of the selected Zephyr fixed-point statement sequence.
Synthetic witnesses test numerical semantics; they are not measured devices.
"""
from __future__ import annotations
from collections.abc import Sequence
from fractions import Fraction

# First byte is register0x31. Width/signs follow the actual Table23, not the
# broad calibration-address heading elsewhere in the document.
LAYOUT = [('t1',2,False),('t2',2,False),('t3',1,True),
          ('p1',2,True),('p2',2,True),('p3',1,True),('p4',1,True),
          ('p5',2,False),('p6',2,False),('p7',1,True),('p8',1,True),
          ('p9',2,True),('p10',1,True),('p11',1,True)]
EXPONENTS = dict(t1=-8,t2=30,t3=48,p1=20,p2=29,p3=32,p4=37,
                 p5=-3,p6=6,p7=8,p8=15,p9=48,p10=48,p11=65)


def _word(value, bits, signed=False):
    low, high = (-(1 << (bits-1)), (1 << (bits-1))-1) if signed else (0,(1<<bits)-1)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Expected {bits}-bit {"signed" if signed else "unsigned"} integer')
    return value


def decode_calibration(data: Sequence[int]) -> dict[str,int]:
    if len(data) != 21:
        raise ValueError('BMP388 trimming image must have21 bytes from0x31 through0x45')
    for byte in data: _word(byte,8)
    result={};cursor=0
    for name,width,signed in LAYOUT:
        word=sum(data[cursor+i] * 256**i for i in range(width))
        if signed and word >= 1 << (8*width-1): word -= 1 << (8*width)
        result[name]=word;cursor+=width
    return result


def encode_calibration(cal: dict[str,int]) -> list[int]:
    result=[]
    for name,width,signed in LAYOUT:
        word=_word(cal[name],width*8,signed) % (1 << (8*width))
        result += [(word >> (8*i)) & 255 for i in range(width)]
    return result


def decode_sample(data: Sequence[int]) -> dict[str,int]:
    if len(data)!=6: raise ValueError('Full pressure/temperature frame requires6 bytes')
    for byte in data: _word(byte,8)
    return {'pressure':data[0]+256*data[1]+65536*data[2],
            'temperature':data[3]+256*data[4]+65536*data[5]}


def encode_sample(raw: dict[str,int]) -> list[int]:
    result=[]
    for name in ('pressure','temperature'):
        word=_word(raw[name],24)
        result += [(word >> (8*i)) & 255 for i in range(3)]
    return result


def quantize(cal: dict[str,int]) -> dict[str,Fraction]:
    encode_calibration(cal) # validate widths and signedness before arithmetic
    result={}
    for name,exp in EXPONENTS.items():
        word=cal[name]-(16384 if name in ('p1','p2') else 0)
        result[name]=Fraction(word,1<<exp) if exp>=0 else Fraction(word*(1<<-exp))
    return result


def compensate(cal: dict[str,int], raw: dict[str,int]) -> dict[str,Fraction]:
    encode_sample(raw)
    q=quantize(cal);u=raw['pressure'];d=Fraction(raw['temperature'])-q['t1']
    temp_linear=d*q['t2'];temp_quadratic=d*d*q['t3'];t=temp_linear+temp_quadratic
    # Group by the pressure power; temperature is the same frame's result.
    c0=sum((q['p5'],q['p6']*t,q['p7']*t**2,q['p8']*t**3))
    c1=sum((q['p1'],q['p2']*t,q['p3']*t**2,q['p4']*t**3))
    c2=q['p9']+q['p10']*t;c3=q['p11']
    terms=[c0,c1*u,c2*u*u,c3*u*u*u]
    return {'temperature_delta':d,'temperature_linear':temp_linear,
            'temperature_quadratic':temp_quadratic,'temperature_c':t,
            'pressure_offset_pa':c0,'pressure_sensitivity':c1,
            'pressure_quadratic_coefficient':c2,'pressure_cubic_coefficient':c3,
            'pressure_linear_pa':terms[1],'pressure_quadratic_pa':terms[2],
            'pressure_cubic_pa':terms[3],'pressure_pa':sum(terms)}


def convert(cal: dict[str,int], raw: dict[str,int]) -> dict[str,int]:
    values=compensate(cal,raw)
    return {'temperature_centi_c':int(values['temperature_c']*100),
            'pressure_hpa':int(values['pressure_pa']/100)}


def decimal_crosscheck(cal: dict[str,int], raw: dict[str,int]):
    """Separate raw-coefficient/Horner expression at100-digit Decimal precision."""
    from decimal import Decimal,localcontext
    with localcontext() as context:
        context.prec=100
        d=Decimal(raw['temperature'])-Decimal(cal['t1'])*256
        t=d*(Decimal(cal['t2'])/(Decimal(2)**30)+d*Decimal(cal['t3'])/(Decimal(2)**48))
        u=Decimal(raw['pressure'])
        p=(Decimal(cal['p5'])*8+t*(Decimal(cal['p6'])/64+t*(Decimal(cal['p7'])/256+t*Decimal(cal['p8'])/32768)))
        p+=u*((Decimal(cal['p1'])-16384)/(Decimal(2)**20)+t*((Decimal(cal['p2'])-16384)/(Decimal(2)**29)+t*(Decimal(cal['p3'])/(Decimal(2)**32)+t*Decimal(cal['p4'])/(Decimal(2)**37))))
        p+=u*u*(Decimal(cal['p9'])/(Decimal(2)**48)+t*Decimal(cal['p10'])/(Decimal(2)**48)+u*Decimal(cal['p11'])/(Decimal(2)**65))
        return t,p


def validate_vectors(path=None):
    import json
    from pathlib import Path
    from decimal import Decimal,localcontext
    path=Path(path) if path else Path(__file__).with_name('cases.yaml')
    cases=json.loads(path.read_text());count=0
    for v in cases['vectors']+cases['oracle_only_vectors']:
        c=cases['calibration_profiles'][v['profile']]['coefficients'];raw=v['raw']
        if encode_calibration(c)!=cases['calibration_profiles'][v['profile']]['bytes']:
            raise AssertionError(v['id']+': calibration byte witness differs')
        if decode_calibration(encode_calibration(c))!=c: raise AssertionError('calibration decode')
        if encode_sample(raw)!=v['raw_bytes'] or decode_sample(v['raw_bytes'])!=raw:
            raise AssertionError(v['id']+': raw byte witness differs')
        values=compensate(c,raw)
        if convert(c,raw)!=v['expected']: raise AssertionError(v['id']+': integer outputs differ')
        if {k:str(x) for k,x in values.items()}!=v['exact_intermediates']:
            raise AssertionError(v['id']+': exact intermediate witness differs')
        dt,dp=decimal_crosscheck(c,raw)
        with localcontext() as context:
            context.prec=100
            for exact,independent in ((values['temperature_c'],dt),(values['pressure_pa'],dp)):
                err=abs(Decimal(exact.numerator)/Decimal(exact.denominator)-independent)
                if err>Decimal('1e-80'): raise AssertionError(v['id']+': Decimal cross-check differs')
        with localcontext() as context:
            context.prec=100
            rounded=[int(dt*100),int(dp/100)]
        if rounded!=[v['expected']['temperature_centi_c'],v['expected']['pressure_hpa']]:
            raise AssertionError(v['id']+': independent rounding witness differs')
        if v.get('manual_exact'):
            for key,value in v['manual_exact'].items():
                if values[key]!=Fraction(value): raise AssertionError(v['id']+': manual arithmetic differs')
        count+=1
    # All signed byte encodings, and every signed/unsigned16-bit pattern,
    # cross-checked against a separate standard-library byte decoder.
    for signed,width in ((False,1),(True,1),(False,2),(True,2)):
        for word in range(1<<(8*width)):
            image=word.to_bytes(width,'little')
            expected=int.from_bytes(image,'little',signed=signed)
            data=[0]*21
            if width==2:
                offset=5 if signed else 0;data[offset:offset+2]=image
                actual=decode_calibration(data)['p1' if signed else 't1']
            elif signed:
                data[4]=word;actual=decode_calibration(data)['t3']
            else:
                actual=decode_sample([word,0,0,0,0,0])['pressure']
            if actual!=expected:raise AssertionError('coefficient bit-pattern mismatch')
    base=cases['calibration_profiles']['linear']['coefficients']
    for invalid in cases['invalid_inputs']:
        c=base.copy();raw={'pressure':8388608,'temperature':9207808}
        key=invalid['kind']
        if key.startswith('raw_'):raw[key[4:]]=invalid['value']
        else:c[key]=invalid['value']
        try:convert(c,raw)
        except ValueError:pass
        else:raise AssertionError('malformed input accepted: '+key)
    for malformed in ([0]*20,[0]*22,[256]+[0]*20,[True]+[0]*20):
        try:decode_calibration(malformed)
        except ValueError:pass
        else:raise AssertionError('malformed calibration image accepted')
    return {'invalid_inputs':len(cases['invalid_inputs'])+4,'vectors':len(cases['vectors']),'oracle_only_vectors':len(cases['oracle_only_vectors']),
            'profiles':len(cases['calibration_profiles']),'coefficient_bit_patterns':131584,
            'decimal_crosschecks':count}


if __name__=='__main__':
    import json
    print(json.dumps(validate_vectors(),sort_keys=True))


def render_vectors(directory):
    import json
    from math import ceil, floor
    cases=json.loads((directory/'cases.yaml').read_text());records=[];lines=[]
    for v in cases['vectors']:
        cal=cases['calibration_profiles'][v['profile']]
        expected=convert(cal['coefficients'],v['raw']);exact=compensate(cal['coefficients'],v['raw'])
        values=[exact['temperature_c']*100,exact['pressure_pa']/100]
        lower=[ceil(x-1) for x in values];upper=[floor(x+1) for x in values]
        e=[expected['temperature_centi_c'],expected['pressure_hpa']]
        lines.append('Vector{id:'+json.dumps(v['id'])+',cal:'+str(cal['bytes'])+',raw:'+str(v['raw_bytes'])+',expected:'+str(e)+',lower:'+str(lower)+',upper:'+str(upper)+'}')
        records.append({**v,'independently_computed':expected,'exact_native':[str(x) for x in values],'lower':lower,'upper':upper,'tolerance_native_units':1})
    return 'const VECTORS:&[Vector]=&[\n'+',\n'.join(lines)+'\n];\n',records
