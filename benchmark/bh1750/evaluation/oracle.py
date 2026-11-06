"""Manufacturer-defined BH1750FVI H-resolution/MTreg69 arithmetic.

ROHM No.11046EDT01, 2011.11 Rev.D, pages 7, 10 and 11: a high-byte-first
unsigned sample divided by 1.2. The selected mode is H-resolution, not
H-resolution2; MTreg69 leaves the sensitivity factor at one. This numeric
oracle contains no source-driver, IR or generated-code dependency.
"""
from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction


def ideal_lux(raw: int) -> Fraction:
    if type(raw) is not int or not 0 <= raw <= 65535:
        raise ValueError('light count must be an integer in 0..65535')
    return Fraction(raw * 5, 6)


def decode_sample(raw_bytes: Sequence[int]) -> dict[str, int]:
    if len(raw_bytes) != 2 or any(type(b) is not int or not 0 <= b <= 255 for b in raw_bytes):
        raise ValueError('expected exactly two unsigned BH1750FVI bytes')
    count = int.from_bytes(bytes(raw_bytes), 'big')
    return {'illuminance_lux': int(ideal_lux(count))}


def encode_sample(raw: dict[str, int]) -> list[int]:
    ideal_lux(raw['light'])
    return list(raw['light'].to_bytes(2, 'big'))


def convert(raw: dict[str, int]) -> dict[str, int]:
    return decode_sample(encode_sample(raw))


def validate_vectors(path=None) -> dict[str, int]:
    import json
    from decimal import Decimal
    from pathlib import Path

    path = Path(path) if path is not None else Path(__file__).with_name('cases.yaml')
    cases = json.loads(path.read_text())
    for vector in cases['vectors']:
        raw = vector['raw']['light']
        if raw != vector['raw_bytes'][0] * 256 + vector['raw_bytes'][1]:
            raise AssertionError(f"{vector['id']}: base-256 packing witness differs")
        if ideal_lux(raw) != Fraction(vector['ideal_lux_fraction']):
            raise AssertionError(f"{vector['id']}: rational witness differs")
        if decode_sample(vector['raw_bytes']) != vector['expected']:
            raise AssertionError(f"{vector['id']}: wrong whole-lux witness")
    for vector in cases['invalid_vectors']:
        try:
            decode_sample(vector['raw_bytes'])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{vector['id']}: malformed sample accepted")
    # A decimal expression independently checks the exact rational equation
    # and the full unsigned width, without reproducing a source C expression.
    for raw in range(65536):
        expected = int(Decimal(raw) / Decimal('1.2'))
        if convert({'light': raw})['illuminance_lux'] != expected:
            raise AssertionError(f'unsigned sample {raw} was changed')
    return {'vectors': len(cases['vectors']), 'invalid_vectors': len(cases['invalid_vectors']),
            'exhaustive_counts': 65536}


def render_vectors(directory):
    import json
    from math import floor, ceil
    validate_vectors(directory/'cases.yaml')
    rows, lines = [], []
    for v in json.loads((directory/'cases.yaml').read_text())['vectors']:
        raw=v['raw']['light'];exact=ideal_lux(raw)
        fields={'id':v['id'],'raw':raw,'lower':max(0,floor(exact)-1),'upper':ceil(exact)+1}
        lines.append('Vector { '+', '.join(k+': '+json.dumps(x) for k,x in fields.items())+' }')
        rows.append({**v,'independently_computed':convert(v['raw']),'lower':fields['lower'],'upper':fields['upper']})
    return 'const VECTORS: &[Vector] = &[\n'+',\n'.join(lines)+'\n];\n', rows


if __name__ == '__main__':
    import json
    print(json.dumps(validate_vectors(), sort_keys=True))
