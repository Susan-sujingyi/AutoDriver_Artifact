"""Independent rational BMM150 compensation from raw Bosch SensorAPI float equations.

Full formula is not in the datasheet; host manufacturer source is not supplied to D."""
from fractions import Fraction as F
FIELDS={'x1':(0,1,True),'y1':(1,1,True),'z4':(5,2,True),'x2':(7,1,True),'y2':(8,1,True),'z2':(11,2,True),'z1':(13,2,False),'xyz1':(15,2,False),'z3':(17,2,True),'xy2':(19,1,True),'xy1':(20,1,False)}
def octets(values,n):
 if len(values)!=n or any(type(v) is not int or not 0<=v<=255 for v in values):raise ValueError('invalid byte encoding')
def encode_trim(t,reserved=0xa5,xyz_reserved=False):
 b=[reserved]*21
 for k,(i,n,signed) in FIELDS.items():
  v=t[k];lo=-(1<<(n*8-1)) if signed else 0;hi=(1<<(n*8-(1 if signed else 0)))-1
  if type(v) is not int or not lo<=v<=hi:raise ValueError('trim range '+k)
  b[i:i+n]=v.to_bytes(n,'little',signed=signed)
 if t['xyz1']>32767:raise ValueError('xyz1 is15bit')
 if xyz_reserved:b[16]|=128
 return b

def decode_trim(b):
 octets(b,21);t={k:int.from_bytes(bytes(b[i:i+n]),'little',signed=signed) for k,(i,n,signed) in FIELDS.items()};t['xyz1']&=32767;return t

def encode_sample(raw,ready=True,selftest=1):
 limits={'x':(-4096,4095,3),'y':(-4096,4095,3),'z':(-16384,16383,1),'hall':(0,16383,2)};out=[]
 for name,(lo,hi,shift) in limits.items():
  v=raw[name]
  if type(v) is not int or not lo<=v<=hi:raise ValueError('sample range '+name)
  packed=((v<<shift)&65535)|(int(ready) if name=='hall' else selftest)
  out+=list(packed.to_bytes(2,'little'))
 return out

def decode_sample(b):
 octets(b,8)
 return {name:int.from_bytes(bytes(b[i:i+2]),'little',signed=name!='hall')>>shift for name,i,shift in [('x',0,3),('y',2,3),('z',4,1),('hall',6,2)]}

def compensate(t,raw):
 encode_trim(t);encode_sample(raw)
 if raw['x']==-4096 or raw['y']==-4096 or raw['z']==-16384:raise ValueError('axis overflow sentinel')
 if not raw['hall'] or not t['xyz1'] or not t['z1'] or not t['z2']:raise ValueError('unusable hall/trim')
 # Algebraic form is independent of the manufacturer's staged floating C.
 ratio=F(t['xyz1'],raw['hall'])-1
 common=t['xy2']*ratio*ratio+t['xy1']*ratio+256
 vals=[]
 for axis in ['x','y']:
  vals.append(F(raw[axis]*(t[axis+'2']+160),131072)*common+F(t[axis+'1'],2))
 denominator=F(t['z2'])+F(t['z1']*raw['hall'],32768)
 if denominator==0:raise ValueError('zero compensation denominator')
 vals.append(F((raw['z']-t['z4'])*131072-t['z3']*(raw['hall']-t['xyz1']),64)/denominator)
 nt=[v*1000 for v in vals]
 if any(not -(1<<31)<=v<(1<<31) for v in nt):raise ValueError('output exceeds signed client representation')
 return {'nanotesla':[int(v) for v in nt],'exact_nanotesla':[{'numerator':v.numerator,'denominator':v.denominator} for v in nt], 'hall_ratio_minus_one':{'numerator':ratio.numerator,'denominator':ratio.denominator},'z_denominator':{'numerator':denominator.numerator,'denominator':denominator.denominator}}


def render_vectors(directory):
 import json
 lines=[];records=[]
 for v in json.loads((directory/'cases.yaml').read_text())['vectors']:
  expected=compensate(decode_trim(v['trim_bytes']),decode_sample(v['bytes']))
  nums=[a['numerator']for a in expected['exact_nanotesla']];dens=[a['denominator']for a in expected['exact_nanotesla']]
  bounds=[abs(n)+(1<<31)*d+1125*d for n,d in zip(nums,dens)]
  if any(d<=0 or b>=(1<<127)for d,b in zip(dens,bounds)):raise ValueError('Exact comparison overflow')
  lines.append('Vector{id:'+json.dumps(v['id'])+',trim:'+str(v['trim_bytes'])+',bytes:'+str(v['bytes'])+',expected:'+str(expected['nanotesla'])+',exact_numerator:'+str(nums)+',exact_denominator:'+str(dens)+'}')
  records.append({**v,'independently_computed':expected,'comparison_i128_abs_bound':bounds})
 return 'const VECTORS:&[Vector]=&[\n'+',\n'.join(lines)+'\n];\n',records


def decimal_compensation(trim, raw):
    """Staged high-precision SensorAPI path, independent of rational algebra."""
    from decimal import Decimal as D, localcontext
    with localcontext() as context:
        context.prec = 80
        ratio = D(trim['xyz1']) * 16384 / raw['hall'] - 16384
        polynomial = D(trim['xy2']) * ratio * ratio / 268435456 + ratio * trim['xy1'] / 16384 + 256
        xy = [(D(raw[a]) * polynomial * (trim[a+'2'] + 160) / 8192 + trim[a+'1'] * 8) / 16
              for a in ('x', 'y')]
        z = ((D(raw['z']) - trim['z4']) * 131072 - D(trim['z3']) * (raw['hall'] - trim['xyz1']))
        z /= 4 * (D(trim['z2']) + D(trim['z1']) * raw['hall'] / 32768)
        return [value * 1000 for value in xy + [z / 16]]


def validate_vectors():
    import json
    from pathlib import Path
    import random
    from decimal import Decimal as D, localcontext
    cases = json.loads(Path(__file__).with_name('cases.yaml').read_text())['vectors']
    rng = random.Random(150)
    checked = 0
    with localcontext() as context:
        context.prec = 80
        for case in cases:
            trim = decode_trim(case['trim_bytes']); raw = decode_sample(case['bytes'])
            assert trim == case['trim'] and raw == case['raw']
            assert decode_trim(encode_trim(trim, xyz_reserved=True)) == trim
            assert compensate(trim, raw)['nanotesla'] == case['expected']['nanotesla']
            samples = [raw] + [dict(x=rng.randrange(-4095,4096), y=rng.randrange(-4095,4096),
                                   z=rng.randrange(-16383,16384), hall=rng.randrange(1,16384)) for _ in range(16)]
            for sample in samples:
                try:
                    exact = compensate(trim, sample)['exact_nanotesla']
                except ValueError:
                    continue
                staged = decimal_compensation(trim, sample)
                for value, pair in zip(staged, exact):
                    assert abs(value - D(pair['numerator']) / pair['denominator']) < D('1e-60')
                checked += 1
    encoded = 0
    base = dict(x=1, y=-2, z=3, hall=10000)
    for name, low, high in (('x',-4096,4096), ('y',-4096,4096), ('z',-16384,16384), ('hall',0,16384)):
        for value in range(low, high):
            raw = dict(base, **{name:value})
            assert decode_sample(encode_sample(raw, ready=False, selftest=0)) == raw
            assert decode_sample(encode_sample(raw, ready=True, selftest=1)) == raw
            encoded += 1
    for name, value in (('x',-4096), ('y',-4096), ('z',-16384), ('hall',0)):
        try:
            compensate(cases[0]['trim'], dict(base, **{name:value}))
        except ValueError:
            pass
        else:
            raise AssertionError('invalid sample accepted: ' + name)
    return {'rational_decimal_crosschecks':checked, 'raw_encodings':encoded,
            'source_vectors':len(cases), 'invalid_sentinel_checks':4}
