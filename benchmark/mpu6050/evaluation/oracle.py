"""Independent MPU6050 ±2g acceleration and native-HIL signed encoding.

Manufacturer product specification Rev3.4 p13 and register map Rev4.2 p29
give 16384 signed counts/g. The benchmark defines g=9.80665m/s² and output
µm/s²; the old Tock NineDof signature itself does not define a unit.
No source driver, method IR, generated implementation or float constants are
used by the rational oracle. Truncation is its canonical integer; public
±1µm/s² tolerance permits other ordinary integer rounding, including the
frozen source's negative arithmetic-shift result.
"""
from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction


def ideal_axis(count: int) -> Fraction:
    if type(count) is not int or not -32768 <= count <= 32767:
        raise ValueError('axis must be a signed 16-bit integer')
    return Fraction(count * 9806650, 16384)


def decode_sample(raw_bytes: Sequence[int]) -> dict[str, list[int]]:
    if len(raw_bytes) != 6 or any(type(b) is not int or not 0 <= b <= 255 for b in raw_bytes):
        raise ValueError('expected exactly six unsigned acceleration bytes')
    data = bytes(raw_bytes)
    values = [int(ideal_axis(int.from_bytes(data[i:i+2], 'big', signed=True))) for i in (0, 2, 4)]
    return {'acceleration_xyz_um_s2': values}


def encode_sample(raw: dict[str, int]) -> list[int]:
    result = []
    for axis in ('x', 'y', 'z'):
        ideal_axis(raw[axis])
        result.extend(raw[axis].to_bytes(2, 'big', signed=True))
    return result


def convert(raw: dict[str, int]) -> dict[str, list[int]]:
    return decode_sample(encode_sample(raw))


def hil_encode(value: int) -> int:
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        raise ValueError('callback value must be signed i32')
    return value & 0xffffffff


def hil_decode(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError('callback word must be unsigned')
    low = value & 0xffffffff
    return low - (1 << 32) if low & (1 << 31) else low


def validate_vectors(path=None) -> dict[str, int]:
    import json
    from decimal import Decimal
    from pathlib import Path

    path = Path(path) if path is not None else Path(__file__).with_name('cases.yaml')
    cases = json.loads(path.read_text())
    for vector in cases['vectors']:
        raw = vector['raw']
        if encode_sample(raw) != vector['raw_bytes']:
            raise AssertionError(f"{vector['id']}: byte packing differs")
        if decode_sample(vector['raw_bytes']) != vector['expected']:
            raise AssertionError(f"{vector['id']}: output witness differs")
        ideals = [ideal_axis(raw[axis]) for axis in ('x', 'y', 'z')]
        if ideals != [Fraction(x) for x in vector['ideal_xyz_fraction']]:
            raise AssertionError(f"{vector['id']}: rational witness differs")
        if [hil_encode(x) for x in vector['expected']['acceleration_xyz_um_s2']] != vector['hil_words']:
            raise AssertionError(f"{vector['id']}: signed ABI witness differs")
    for vector in cases['invalid_vectors']:
        try:
            decode_sample(vector['raw_bytes'])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{vector['id']}: malformed frame accepted")
    # Cross-check all encodings against base256 arithmetic and decimal SI units.
    for bits in range(65536):
        hi, lo = divmod(bits, 256)
        count = (hi - 256 if hi >= 128 else hi) * 256 + lo
        expected = int(Decimal(count) / Decimal(16384) * Decimal('9.80665') * Decimal(1000000))
        actual = decode_sample([hi, lo] * 3)['acceleration_xyz_um_s2']
        if actual != [expected] * 3 or hil_decode(hil_encode(expected)) != expected:
            raise AssertionError(f'16-bit sample {bits} differs')
    return {'vectors': len(cases['vectors']), 'invalid_vectors': len(cases['invalid_vectors']),
            'exhaustive_axis_encodings': 65536, 'axes_checked_per_encoding': 3}


if __name__ == '__main__':
    import json
    print(json.dumps(validate_vectors(), sort_keys=True))


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
