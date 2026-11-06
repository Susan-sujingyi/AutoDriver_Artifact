"""ADI ADT7310 RevA temperature coding: independent rational centiCelsius."""
from fractions import Fraction
from decimal import Decimal
from pathlib import Path
from math import ceil, floor
import json


def ideal(raw):
    if type(raw) is not int or not -32768 <= raw <= 32767:
        raise ValueError('Signed 16-bit count required')
    return Fraction(raw * 100, 128)


def validate_vectors():
    for raw in range(-32768, 32768):
        assert int(ideal(raw)) == int(Decimal(raw) * Decimal(100) / Decimal(128))
    return {'exhaustive_encodings': 65536, 'scope': 'encoding/arithmetic, not physical sensor range'}


def render_vectors(directory):
    records, rows = [], []
    for item in json.loads((Path(directory)/'cases.yaml').read_text())['vectors']:
        raw = item['raw_count']
        if not -7040 <= raw <= 19200: raise ValueError('Core vector outside physical temperature range')
        value = ideal(raw)
        lo, hi = ceil(value-1), floor(value+1)
        records.append({**item, 'ideal_centiC': str(value), 'lower': lo, 'upper': hi})
        rows.append('Vector { id: '+json.dumps(item['id'])+f', raw: {raw}, lower: {lo}, upper: {hi}'+' }')
    return 'const VECTORS: &[Vector] = &[\n'+',\n'.join(rows)+'\n];\n', records
