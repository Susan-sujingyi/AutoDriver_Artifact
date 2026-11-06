"""Independent ISL29035 ALS oracle, Renesas/Intersil FN8371 Rev 3.00.

Page 12 defines unsigned, right-aligned ADC data and the range/resolution
settings. Page 14 Equation 3 defines lux = range * DATA / 2**n. The selected
configuration is 4000 lux, eight ADC bits, continuous ALS, 410 us integration.
No source driver, reference driver, IR or method rules are imported.
"""
from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction


RANGE_LUX = 4000
ADC_BITS = 8
INTEGRATION_TIME_US = 410


def _octets(values: Sequence[int]) -> bytes:
    if len(values) not in (1, 2):
        raise ValueError("expected DATALSB, optionally followed by DATAMSB")
    if any(type(value) is not int or not 0 <= value <= 255 for value in values):
        raise ValueError("register bytes must be integers in 0..255")
    return bytes(values)


def decode_lux(raw_adc: int, *, range_lux: int = RANGE_LUX,
               adc_bits: int = ADC_BITS) -> Fraction:
    """Return exact lux using the datasheet equation, without HIL rounding.

Other documented settings are accepted for oracle cross-checks only. Their
availability here does not make them legal in the fixed benchmark task.
    """
    if type(adc_bits) is not int or adc_bits not in (4, 8, 12, 16):
        raise ValueError("ADC resolution must be 4, 8, 12 or 16 bits")
    if type(range_lux) is not int or range_lux not in (1000, 4000, 16000, 64000):
        raise ValueError("range must be 1000, 4000, 16000 or 64000 lux")
    if type(raw_adc) is not int or not 0 <= raw_adc < 2**adc_bits:
        raise ValueError("ADC count is outside the selected unsigned width")
    return Fraction(range_lux * raw_adc, 2**adc_bits)


def decode_sample(raw_bytes: Sequence[int]) -> dict[str, int]:
    """Decode selected eight-bit data from address order 0x02, then 0x03.

Only D0..D7 carry the selected ADC result (p. 12). Reading DATALSB alone is
sufficient. DATAMSB, if supplied, is ignored; no claim that unused bits must
    be zero is made. v2 accepts adjacent integer lux quantization choices.
"""
    raw_adc = _octets(raw_bytes)[0]
    exact = decode_lux(raw_adc)
    return {
        "raw_adc": raw_adc,
        "illuminance_lux": int(exact),
        "illuminance_lux_numerator": exact.numerator,
        "illuminance_lux_denominator": exact.denominator,
    }


def encode_sample(raw: dict[str, int]) -> list[int]:
    """Construct default model bytes; do not wrap invalid input counts."""
    count = raw["light"]
    decode_lux(count)
    return [count, 0]


def convert(raw: dict[str, int]) -> dict[str, int]:
    """Convenience entry point for a benchmark vector's raw fields."""
    return decode_sample(encode_sample(raw))


def validate_vectors(path=None) -> dict[str, int]:
    """Check frozen expected fractions and decimal derivations independently."""
    import json
    from decimal import Decimal
    from pathlib import Path

    path = Path(path) if path is not None else Path(__file__).with_name("cases.yaml")
    cases = json.loads(path.read_text())
    for vector in cases["vectors"]:
        actual = convert(vector["raw"])
        if actual != vector["expected"]:
            raise AssertionError(f"{vector['id']}: {actual} != {vector['expected']}")
        if encode_sample(vector["raw"]) != vector["raw_bytes"]:
            raise AssertionError(f"{vector['id']}: byte packing")
        # This second calculation follows the independently reduced scale
        # 15.625 lux/count, using decimal arithmetic rather than Fraction.
        decimal_lux = Decimal(vector["raw"]["light"]) * Decimal("15.625")
        if decimal_lux != Decimal(vector["exact_lux_decimal"]):
            raise AssertionError(f"{vector['id']}: decimal derivation")
        if int(decimal_lux) != actual["illuminance_lux"]:
            raise AssertionError(f"{vector['id']}: integer rounding")
        if decode_sample(vector["raw_bytes"][:1]) != actual:
            raise AssertionError(f"{vector['id']}: legal low-byte-only acquisition")
    for vector in cases["oracle_only_vectors"]:
        exact = decode_lux(vector["raw_adc"], range_lux=vector["range_lux"],
                           adc_bits=vector["adc_bits"])
        if (exact.numerator, exact.denominator) != tuple(vector["expected_fraction"]):
            raise AssertionError(f"{vector['id']}: alternate setting equation")
    return {name: len(cases[name]) for name in ("vectors", "oracle_only_vectors")}


def render_vectors(directory):
    import json
    records, lines = [], []
    for v in json.loads((directory/'cases.yaml').read_text())['vectors']:
        expected = convert(v['raw'])
        exact = decode_lux(v['raw']['light'])
        lower = max(0, int(exact)-1)
        upper = -(-exact.numerator//exact.denominator)+1
        fields = {'id':v['id'],'raw':v['raw']['light'],'lower':lower,'upper':upper}
        lines.append('Vector { '+', '.join(k+': '+json.dumps(value) for k,value in fields.items())+' }')
        records.append({**v,'independently_computed':expected,'lower':lower,'upper':upper})
    return 'const VECTORS: &[Vector] = &[\n'+',\n'.join(lines)+'\n];\n', records


if __name__ == "__main__":
    import json
    print(json.dumps(validate_vectors(), sort_keys=True))
