"""DHT20 manufacturer equations and frame integrity, independent of driver IR.

V1.0 May2021 p10–11: status + two packed20bit words, CRC of all six bytes.
The CRC oracle is polynomial long division, separate from the reference's
byte/bit shift register. Numeric values are exact rational centi-units.
"""
from fractions import Fraction


def crc8(data):
    if not data or any(type(x) is not int or not 0 <= x <= 255 for x in data):
        raise ValueError('CRC input must be nonempty unsigned bytes')
    remainder = (int.from_bytes(bytes(data), 'big') << 8) ^ (0xff << (8 * len(data)))
    while remainder.bit_length() > 8:
        remainder ^= 0x131 << (remainder.bit_length() - 9)
    return remainder


def ideal(raw):
    for key in ('temperature', 'humidity'):
        if type(raw[key]) is not int or not 0 <= raw[key] < (1 << 20):
            raise ValueError('raw word must be unsigned20bit')
    return {'temperature_centiCelsius': Fraction(raw['temperature'] * 20000, 1 << 20) - 5000,
            'humidity_hundredths_percent': Fraction(raw['humidity'] * 10000, 1 << 20)}


def convert(raw):
    return {key: int(value) for key, value in ideal(raw).items()}


def encode_sample(raw, status=0x18):
    ideal(raw)
    if type(status) is not int or not 0 <= status <= 255:
        raise ValueError('invalid status byte')
    packed = (raw['humidity'] << 20) | raw['temperature']
    frame = [status, *packed.to_bytes(5, 'big')]
    return frame + [crc8(frame)]


def decode_sample(frame):
    if len(frame) != 7 or any(type(x) is not int or not 0 <= x <= 255 for x in frame):
        raise ValueError('expected seven unsigned frame bytes')
    if frame[0] & 0x80:
        raise ValueError('measurement still busy')
    if crc8(frame[:6]) != frame[6]:
        raise ValueError('bad frame CRC')
    packed = int.from_bytes(bytes(frame[1:6]), 'big')
    return convert({'temperature': packed & ((1 << 20)-1), 'humidity': packed >> 20})


def validate_vectors(path=None):
    import json
    from pathlib import Path
    from decimal import Decimal
    path = Path(path) if path else Path(__file__).with_name('cases.yaml')
    cases = json.loads(path.read_text())
    def shift_crc(data):
        value = 255
        for byte in data:
            value ^= byte
            for _ in range(8):
                value = ((value << 1) ^ (0x31 if value & 0x80 else 0)) & 255
        return value
    for vector in cases['vectors'] + cases['oracle_only_vectors']:
        assert encode_sample(vector['raw'], vector['status']) == vector['raw_bytes'], vector['id']
        assert decode_sample(vector['raw_bytes']) == vector['expected'], vector['id']
        assert {k: str(v) for k, v in ideal(vector['raw']).items()} == vector['ideal_fractions'], vector['id']
        for byte in range(7):
            for bit in range(8):
                bad = vector['raw_bytes'].copy(); bad[byte] ^= 1 << bit
                try:
                    decode_sample(bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError('single-bit integrity error was not rejected')
    # Exercise every humidity/temperature shared-nibble pairing and low16 bits.
    for n in range(65536):
        h = ((n & 0xfff) << 8) | (n >> 8)
        t = (n << 4) | (n >> 12)
        frame = encode_sample({'temperature': t, 'humidity': h})
        assert crc8(frame[:6]) == shift_crc(frame[:6])
        expected = {'temperature_centiCelsius': int(Decimal(t)*Decimal(20000)/Decimal(1048576)-5000),
                    'humidity_hundredths_percent': int(Decimal(h)*Decimal(10000)/Decimal(1048576))}
        assert decode_sample(frame) == expected
    return {'rated_vectors': len(cases['vectors']), 'oracle_only_vectors': len(cases['oracle_only_vectors']),
            'packing_crc_decimal_crosschecks': 65536,
            'single_bit_integrity_checks': 56*(len(cases['vectors'])+len(cases['oracle_only_vectors']))}


if __name__ == '__main__':
    import json
    print(json.dumps(validate_vectors(), sort_keys=True))


def render_vectors(directory):
    import json
    records=[];lines=[]
    for v in json.loads((directory/'cases.yaml').read_text())['vectors']:
        expected=decode_sample(v['raw_bytes']); exact=ideal(v['raw'])
        nums=[int(exact[k]*1048576) for k in ('temperature_centiCelsius','humidity_hundredths_percent')]
        lines.append('Vector{id:'+json.dumps(v['id'])+',frame:'+str(v['raw_bytes'])+',numerator:'+str(nums)+'}')
        records.append({**v,'independently_computed':expected,'exact_numerators':nums,'denominator':1048576,'tolerance_native_units':1})
    return 'const VECTORS:&[Vector]=&[\n'+',\n'.join(lines)+'\n];\n',records
