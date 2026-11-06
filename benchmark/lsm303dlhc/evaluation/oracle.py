"""Independent rational conversion: ST DocID018771 Rev2 pp38-39, Table75.

No reference driver, Zephyr conversion code or method IR is imported.
Input order is XYZ; wire order is X,Z,Y, big endian signed16.
One gauss is exactly 100000 nT. Tolerance is arithmetic rounding, not accuracy.
"""
from fractions import Fraction
from decimal import Decimal, localcontext
from math import ceil, floor
from pathlib import Path
import json


def ideal(xyz):
    if len(xyz) != 3 or any(type(v) is not int or not -2048 <= v <= 2047 for v in xyz):
        raise ValueError('Three documented signed output counts required')
    return [Fraction(v * 100000, sensitivity) for v, sensitivity in zip(xyz, (1100, 1100, 980))]


def wire(xyz):
    ideal(xyz)
    return list(b''.join(xyz[i].to_bytes(2, 'big', signed=True) for i in (0, 2, 1)))


def decode(data):
    if len(data) != 6 or any(type(x) is not int or not 0 <= x <= 255 for x in data):
        raise ValueError('Six bytes required')
    data = bytes(data)
    return [int.from_bytes(data[i:i+2], 'big', signed=True) for i in (0, 4, 2)]


def validate_vectors():
    vectors = json.loads(Path(__file__).with_name('cases.yaml').read_text())['vectors']
    for v in vectors:
        assert decode(wire(v['xyz_counts'])) == v['xyz_counts']
    with localcontext() as ctx:
        ctx.prec = 40
        for raw in range(-2048, 2048):
            values = ideal([raw] * 3)
            for v, scale in zip(values, (1100, 1100, 980)):
                assert int(v) == int(Decimal(raw) * Decimal(100000) / Decimal(scale))
    return {'vectors': len(vectors), 'exhaustive_documented_counts': 4096}


def render_vectors(directory):
    records = []
    rows = []
    for item in json.loads((Path(directory) / 'cases.yaml').read_text())['vectors']:
        values = ideal(item['xyz_counts'])
        lower, upper = [ceil(v-1) for v in values], [floor(v+1) for v in values]
        raw = wire(item['xyz_counts'])
        records.append({**item, 'raw_bytes': raw, 'ideal_nT': [str(x) for x in values],
                        'lower': lower, 'upper': upper})
        rows.append('Vector { id: '+json.dumps(item['id'])+', raw: '+str(raw)+
                    ', lower: '+str(lower)+', upper: '+str(upper)+' }')
    return 'const VECTORS: &[Vector] = &[\n'+',\n'.join(rows)+'\n];\n', records
