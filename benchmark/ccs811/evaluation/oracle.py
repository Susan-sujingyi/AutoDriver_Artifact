"""Independent CCS811 gas-result byte oracle, ams DS000459 v1-06.

Datasheet p. 20 defines two unsigned big-endian words in ppm and ppb. The
CCS811 firmware already computes equivalent gas values; the host does not
derive either value from the optional raw ADC/current fields. This oracle
does not use a source driver, reference driver, method IR or generated output.
"""
from __future__ import annotations

from collections.abc import Sequence


def _unsigned(value: int, bits: int, field: str) -> int:
    if type(value) is not int or not 0 <= value < 2**bits:
        raise ValueError(f"{field} must be an unsigned {bits}-bit integer")
    return value


def decode_sample(raw_bytes: Sequence[int]) -> dict[str, int]:
    """Decode both gas channels from 4..8 ALG_RESULT_DATA mailbox bytes.

    Transport decoding preserves any u16 word. Validity/status and the
    separately documented algorithm-output ranges must be checked separately.
    Optional trailing status, error and ADC bytes never alter the gas values.
    """
    if not 4 <= len(raw_bytes) <= 8:
        raise ValueError("both gas channels require 4..8 mailbox bytes")
    for byte in raw_bytes:
        _unsigned(byte, 8, "mailbox byte")
    return {"co2_ppm": 256 * raw_bytes[0] + raw_bytes[1],
            "tvoc_ppb": 256 * raw_bytes[2] + raw_bytes[3]}


def encode_sample(raw: dict[str, int], *, status: int = 0x98, error: int = 0,
                  raw_sensor: int = 0) -> list[int]:
    """Pack an eight-byte mailbox, including deliberately invalid-range words."""
    co2 = _unsigned(raw["co2"], 16, "co2")
    tvoc = _unsigned(raw["tvoc"], 16, "tvoc")
    sensor = _unsigned(raw_sensor, 16, "raw_sensor")
    return [co2 >> 8, co2 & 0xff, tvoc >> 8, tvoc & 0xff,
            _unsigned(status, 8, "status"), _unsigned(error, 8, "error"),
            sensor >> 8, sensor & 0xff]


def convert(raw: dict[str, int]) -> dict[str, int]:
    """Map raw register words to native whole ppm/ppb without scaling."""
    return decode_sample(encode_sample(raw))


def in_output_range(values: dict[str, int]) -> bool:
    """Manufacturer algorithm ranges from p. 12; not an accuracy guarantee."""
    co2, tvoc = values["co2_ppm"], values["tvoc_ppb"]
    return (type(co2) is int and type(tvoc) is int
            and 400 <= co2 <= 29206 and 0 <= tvoc <= 32768)


def validate_vectors(path=None) -> dict[str, int]:
    """Recheck frozen byte witnesses and the full unsigned transport domain."""
    import json
    from pathlib import Path

    path = Path(path) if path is not None else Path(__file__).with_name("cases.yaml")
    cases = json.loads(path.read_text())
    for vector in cases["vectors"] + cases["out_of_range_vectors"]:
        packed = encode_sample(vector["raw"], **vector["metadata"])
        if packed != vector["raw_bytes"]:
            raise AssertionError(f"{vector['id']}: packed bytes differ")
        for length in (4, 5, 6, 7, 8):
            if decode_sample(packed[:length]) != vector["expected"]:
                raise AssertionError(f"{vector['id']}: decoded values differ")
        # Cross-check the frozen byte image with Python's independent byte API.
        independent = {"co2_ppm": int.from_bytes(bytes(packed[:2]), "big"),
                       "tvoc_ppb": int.from_bytes(bytes(packed[2:4]), "big")}
        if independent != vector["expected"]:
            raise AssertionError(f"{vector['id']}: independent byte-order witness differs")
        if in_output_range(independent) is not vector["in_output_range"]:
            raise AssertionError(f"{vector['id']}: algorithm range differs")
    for vector in cases["invalid_vectors"]:
        try:
            convert(vector["raw"])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{vector['id']}: accepted malformed word")
    for word in range(65536):
        packed = bytes([word >> 8, word & 0xff]) * 2
        if decode_sample(packed) != {"co2_ppm": word, "tvoc_ppb": word}:
            raise AssertionError(f"unsigned word {word} was altered")
    return {"vectors": len(cases["vectors"]),
            "out_of_range_vectors": len(cases["out_of_range_vectors"]),
            "invalid_vectors": len(cases["invalid_vectors"]),
            "exhaustive_unsigned_words": 65536}


def render_vectors(directory):
    import json
    records, code = [], ''
    for key, name in [('vectors', 'VECTORS'), ('out_of_range_vectors', 'INVALID_OUTPUTS')]:
        lines = []
        for v in json.loads((directory/'cases.yaml').read_text())[key]:
            expected = convert(v['raw'])
            packed = encode_sample(v['raw'], **v['metadata'])
            if packed != v['raw_bytes']: raise ValueError('Vector byte image mismatch')
            lines.append('Vector { id: '+json.dumps(v['id'])+', bytes: '+str(packed)
                         +', expected: '+str([expected['co2_ppm'],expected['tvoc_ppb']])+' }')
            records.append({**v,'independently_computed':expected})
        code += 'const '+name+': &[Vector] = &[\n'+',\n'.join(lines)+'\n];\n'
    return code, records


if __name__ == "__main__":
    import json
    print(json.dumps(validate_vectors(), sort_keys=True))
