"""Independent SHT30 unsigned signal/CRC oracle, SHT3x-DIS v7 p14."""
from fractions import Fraction


def crc(data):
    if len(data) != 2 or any(type(x) is not int or not 0 <= x <= 255 for x in data):
        raise ValueError('CRC protects exactly two unsigned bytes')
    # Polynomial long division, independent of the driver's bit-update loop.
    polynomial = (0xff << 16) ^ ((data[0] * 256 + data[1]) << 8)
    for shift in range(15, -1, -1):
        if polynomial & (1 << (shift + 8)):
            polynomial ^= 0x131 << shift
    return polynomial


def raw_word(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError('signal must be an unsigned 16-bit integer')
    return [value // 256, value % 256]


def encode_sample(raw):
    t, h = raw_word(raw['temperature']), raw_word(raw['humidity'])
    return t + [crc(t)] + h + [crc(h)]


def convert(raw):
    raw_word(raw['temperature']); raw_word(raw['humidity'])
    t = Fraction(17500 * raw['temperature'], 65535) - 4500
    h = Fraction(10000 * raw['humidity'], 65535)
    return {'temperature_centi_c': t.numerator // t.denominator,
            'humidity_centi_percent': h.numerator // h.denominator}


def decode_sample(data, channel=None):
    if len(data) not in (3, 6) or any(type(x) is not int or not 0 <= x <= 255 for x in data):
        raise ValueError('expected three or six unsigned bytes')
    if channel not in (None, 'temperature', 'humidity'):
        raise ValueError('unknown channel')
    if channel != 'humidity' and crc(data[:2]) != data[2]:
        raise ValueError('temperature CRC')
    if channel != 'temperature':
        if len(data) != 6 or crc(data[3:5]) != data[5]:
            raise ValueError('humidity CRC')
    raw = {'temperature': data[0] * 256 + data[1],
           'humidity': data[3] * 256 + data[4] if len(data) == 6 else 0}
    values = convert(raw)
    if channel == 'temperature':return {'temperature_centi_c': values['temperature_centi_c']}
    if channel == 'humidity':return {'humidity_centi_percent': values['humidity_centi_percent']}
    return values


def validate_vectors():
    import json
    from pathlib import Path
    vectors = json.loads(Path(__file__).with_name('cases.yaml').read_text())['vectors']
    assert crc([0xbe, 0xef]) == 0x92
    for v in vectors:
        assert encode_sample(v['raw']) == v['bytes']
        assert decode_sample(v['bytes']) == v['expected']
        for channel, byte in [('temperature', 2), ('humidity', 5)]:
            data = list(v['bytes']); data[byte] ^= 1
            try:decode_sample(data, channel)
            except ValueError:pass
            else:raise AssertionError('corrupt CRC accepted')
    # Independent conventional CRC implementation exhaustively cross-checks
    # long division, without importing reference or method implementation.
    for raw in range(65536):
        data = raw_word(raw); check = 255
        for value in data:
            check ^= value
            for _ in range(8):check = ((check << 1) ^ (0x31 if check & 128 else 0)) & 255
        assert crc(data) == check
    return {'vectors': len(vectors), 'crc_word_checks': 65536, 'manufacturer_crc_beef': 146}


def render_vectors(directory):
    import json
    from math import ceil, floor
    data=json.loads((directory/'cases.yaml').read_text())
    validate_vectors()
    rows, declarations=[], []
    for key,name in [('vectors','VECTORS'),('encoding_boundary_vectors','ENCODING')]:
        lines=[]
        for v in data[key]:
            raw=v['raw']; t=Fraction(17500*raw['temperature'],65535)-4500; h=Fraction(10000*raw['humidity'],65535)
            lower=[ceil(t-1),max(0,ceil(h-1))];upper=[floor(t+1),min(10000,floor(h+1))]
            assert encode_sample(raw)==v['bytes']
            lines.append('Vector { id: '+json.dumps(v['id'])+', bytes: '+str(v['bytes'])+', lower: '+str(lower)+', upper: '+str(upper)+' }')
            rows.append({**v,'scope':key,'exact_temperature':str(t),'exact_humidity':str(h),'lower':lower,'upper':upper})
        declarations.append('const '+name+': &[Vector] = &[\n'+',\n'.join(lines)+'\n];\n')
    return ''.join(declarations), rows


if __name__ == '__main__':
    import json
    print(json.dumps(validate_vectors(), sort_keys=True))
