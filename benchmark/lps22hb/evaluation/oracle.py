"""Independent LPS22HB numeric oracle, ST DocID027083 Rev 6, pp. 10, 15, 45–46.

The datasheet defines signed 24-bit pressure / 4096 hPa and signed 16-bit
temperature / 100 degrees C. No source driver, IR, method rules or Rust reference
is imported. Fractions retain all pressure resolution before native HIL rounding.
Encoding extrema are decodable but are not thereby rated physical measurements.
"""
from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction


def _octets(values: Sequence[int], length: int) -> bytes:
    if len(values) != length:
        raise ValueError(f"expected {length} register bytes")
    if any(type(value) is not int or not 0 <= value <= 255 for value in values):
        raise ValueError("register bytes must be integers in 0..255")
    return bytes(values)


def decode_pressure(raw_bytes: Sequence[int]) -> Fraction:
    """PRESS_OUT_XL, PRESS_OUT_L, PRESS_OUT_H -> exact signed pressure in hPa."""
    raw = int.from_bytes(_octets(raw_bytes, 3), byteorder="little", signed=True)
    return Fraction(raw, 4096)


def decode_temperature(raw_bytes: Sequence[int]) -> int:
    """TEMP_OUT_L, TEMP_OUT_H -> signed hundredths of one degree Celsius."""
    return int.from_bytes(_octets(raw_bytes, 2), byteorder="little", signed=True)


def decode_sample(raw_bytes: Sequence[int]) -> dict[str, int]:
    """Decode bytes in register-address order (this is not a legal I2C burst).

    pressure_hpa is truncation toward zero for an integer HIL value. A caller
    enforcing a integer rounding tolerance may compare against the exact rational
    numerator/denominator instead. Negative pressure values cannot be returned
    as a valid native Tock unsigned PressureClient value.
    """
    octets = _octets(raw_bytes, 5)
    pressure = decode_pressure(octets[:3])
    return {
        "pressure_raw_signed": int.from_bytes(octets[:3], "little", signed=True),
        "pressure_hpa": int(pressure),
        "pressure_hpa_numerator": pressure.numerator,
        "pressure_hpa_denominator": pressure.denominator,
        "temperature_centi_c": decode_temperature(octets[3:]),
    }


def in_rated_range(raw_bytes: Sequence[int]) -> bool:
    """ST p. 10 operating ranges; this is not a claim of full-range accuracy."""
    octets = _octets(raw_bytes, 5)
    return (260 <= decode_pressure(octets[:3]) <= 1260
            and -4000 <= decode_temperature(octets[3:]) <= 8500)


def encode_sample(raw: dict[str, int]) -> list[int]:
    """Construct model registers from independent, explicitly specified raw data.

    pressure is its 24-bit register bit pattern (0..0xffffff); temperature is
    signed raw centi-C (-32768..32767). No wrapping of out-of-width inputs occurs.
    """
    pressure, temperature = raw["pressure"], raw["temperature"]
    if type(pressure) is not int or not 0 <= pressure <= 0xffffff:
        raise ValueError("pressure must be a 24-bit register bit pattern")
    if type(temperature) is not int or not -32768 <= temperature <= 32767:
        raise ValueError("temperature must be a signed 16-bit value")
    return list(pressure.to_bytes(3, "little")
                + temperature.to_bytes(2, "little", signed=True))


def convert(raw: dict[str, int]) -> dict[str, int]:
    """Convenience entry point for a benchmark vector's raw fields."""
    return decode_sample(encode_sample(raw))


def validate_vectors(path=None) -> dict[str, int]:
    """Cross-check independently frozen expected values and byte packing."""
    import json
    from pathlib import Path

    path = Path(path) if path is not None else Path(__file__).with_name("cases.yaml")
    cases = json.loads(path.read_text())
    for group in ("vectors", "encoding_boundary_vectors"):
        for vector in cases[group]:
            if encode_sample(vector["raw"]) != vector["raw_bytes"]:
                raise AssertionError(f"{vector['id']}: byte packing")
            actual = decode_sample(vector["raw_bytes"])
            if actual != vector["expected"]:
                raise AssertionError(f"{vector['id']}: {actual} != {vector['expected']}")
            if in_rated_range(vector["raw_bytes"]) != vector["within_rated_range"]:
                raise AssertionError(f"{vector['id']}: physical classification")
    return {name: len(cases[name]) for name in ("vectors", "encoding_boundary_vectors")}


if __name__ == "__main__":
    import json
    print(json.dumps(validate_vectors(), sort_keys=True))


def render_vectors(directory):
    import json
    from math import floor, ceil
    validate_vectors(directory/'cases.yaml')
    rows, lines = [], []
    for v in json.loads((directory/'cases.yaml').read_text())['vectors']:
        expected=decode_sample(v['raw_bytes']);p=decode_pressure(v['raw_bytes'][:3]);t=decode_temperature(v['raw_bytes'][3:])
        lower=[t,max(0,floor(p)-1)];upper=[t,ceil(p)+1]
        lines.append('Vector { id: '+json.dumps(v['id'])+', bytes: '+str(v['raw_bytes'])+', lower: '+str(lower)+', upper: '+str(upper)+' }')
        rows.append({**v,'independently_computed':expected,'lower':lower,'upper':upper})
    return 'const VECTORS: &[Vector] = &[\n'+',\n'.join(lines)+'\n];\n',rows
