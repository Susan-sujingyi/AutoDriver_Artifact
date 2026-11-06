"""Independent APDS-9960 raw proximity oracle, AV02-4191EN (2015-11-13).

The manufacturer's p. 26 PDATA definition and p. 11 signal path specify an
unsigned eight-bit result. Native Tock ProximityClient takes that raw u8.
There is no universal conversion from this count to millimetres, lux or a
percentage. No source driver, reference, AutoDriver IR or method code is used.
"""
from __future__ import annotations

from collections.abc import Sequence


def decode_proximity(raw: int) -> int:
    """Validate and preserve one unsigned proximity count, including 0 and 255."""
    if type(raw) is not int or not 0 <= raw <= 255:
        raise ValueError("proximity count must be an integer in 0..255")
    return raw


def decode_sample(raw_bytes: Sequence[int]) -> dict[str, int]:
    """Decode the single PDATA byte at 0x9c; status is checked by the protocol model."""
    if len(raw_bytes) != 1:
        raise ValueError("expected exactly one PDATA register byte")
    return {"proximity": decode_proximity(raw_bytes[0])}


def encode_sample(raw: dict[str, int]) -> list[int]:
    """Build a model PDATA image without wrapping out-of-width input values."""
    return [decode_proximity(raw["proximity"])]


def convert(raw: dict[str, int]) -> dict[str, int]:
    """Convenience entry point for a benchmark vector's raw fields."""
    return decode_sample(encode_sample(raw))


def validate_vectors(path=None) -> dict[str, int]:
    """Check explicit binary/decimal witnesses and every representable count."""
    import json
    from pathlib import Path

    path = Path(path) if path is not None else Path(__file__).with_name("cases.yaml")
    cases = json.loads(path.read_text())
    for vector in cases["vectors"]:
        if encode_sample(vector["raw"]) != vector["raw_bytes"]:
            raise AssertionError(f"{vector['id']}: byte encoding")
        result = decode_sample(vector["raw_bytes"])
        if result != vector["expected"]:
            raise AssertionError(f"{vector['id']}: {result} != {vector['expected']}")
        # Reviewable independent bit-weight expansion of the frozen byte.
        binary = vector["binary"]
        if len(binary) != 8 or any(bit not in "01" for bit in binary):
            raise AssertionError(f"{vector['id']}: invalid binary witness")
        expanded = sum(int(bit) * 2**position for position, bit in enumerate(reversed(binary)))
        if expanded != vector["expected"]["proximity"]:
            raise AssertionError(f"{vector['id']}: binary/decimal witness differs")
    for vector in cases["invalid_vectors"]:
        try:
            convert(vector["raw"])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{vector['id']}: accepted invalid count")
    for count in range(256):
        if decode_sample(bytes([count])) != {"proximity": count}:
            raise AssertionError(f"unsigned byte {count} was altered")
    return {"vectors": len(cases["vectors"]), "invalid_vectors": len(cases["invalid_vectors"]),
            "exhaustive_counts": 256}


def render_vectors(directory):
    """Render the unchanged v2 vectors for the common executor."""
    import json
    validate_vectors(directory / 'cases.yaml')
    rows, lines = [], []
    for vector in json.loads((directory / 'cases.yaml').read_text())['vectors']:
        expected = decode_sample(vector['raw_bytes'])['proximity']
        lines.append('Vector { id: ' + json.dumps(vector['id']) + ', raw: '
                     + str(vector['raw_bytes'][0]) + ', expected: ' + str(expected) + ' }')
        rows.append({**vector, 'independently_computed': {'proximity': expected}})
    return 'const VECTORS: &[Vector] = &[\n' + ',\n'.join(lines) + '\n];\n', rows


if __name__ == "__main__":
    import json
    print(json.dumps(validate_vectors(), sort_keys=True))
