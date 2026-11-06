"""DHT11 integer encoding from the frozen original document, independent of driver."""
import json
from pathlib import Path

def decode_frame(frame):
    """Original manual p3: five MSB-first bytes; fractions are zero here."""
    if len(frame) != 5 or any(type(value) is not int or not 0 <= value < 256 for value in frame):
        raise ValueError('Expected five unsigned frame bytes')
    if sum(frame[:4]) % 256 != frame[4]:
        raise ValueError('Checksum mismatch')
    if frame[1] or frame[3]:
        raise ValueError('Selected original DHT11 format has zero fractional bytes')
    return {'expected_temperature': frame[2] * 100, 'expected_humidity': frame[0] * 100}


def validate_vectors():
    rows = json.loads(Path(__file__).with_name('cases.yaml').read_text())['vectors']
    for row in rows:
        t, h = row['temperature'], row['humidity']
        if type(t) is not int or type(h) is not int or not 0 <= t <= 50 or not 20 <= h <= 90:
            raise ValueError('Outside selected original DHT11 nominal range')
        frame = [h, 0, t, 0, (t + h) % 256]
        assert decode_frame(frame) == {'expected_temperature': t * 100, 'expected_humidity': h * 100}
        word = int.from_bytes(bytes(frame), 'big')
        for bit in range(40):
            damaged = list((word ^ (1 << bit)).to_bytes(5, 'big'))
            try:
                decode_frame(damaged)
            except ValueError:
                pass
            else:
                raise AssertionError('Single-bit frame damage accepted')
    # Independent place-value packing over every selected integer combination.
    for t in range(51):
        for h in range(20, 91):
            packed = h * 256**4 + t * 256**2 + (t + h) % 256
            assert decode_frame(list(packed.to_bytes(5, 'big'))) == {
                'expected_temperature': t * 100, 'expected_humidity': h * 100}
    return {'vectors': len(rows), 'single_bit_checks': 40 * len(rows),
            'integer_encoding_crosschecks': 51 * 71}


def render_vectors(directory):
    rows=json.loads((Path(directory)/'cases.yaml').read_text())['vectors']
    for r in rows:
        if not 0 <= r['temperature'] <= 50 or not 20 <= r['humidity'] <= 90:
            raise ValueError('Outside selected original DHT11 nominal range')
    text='const VECTORS: &[Vector] = &[\n'+',\n'.join(
        'Vector { id: '+json.dumps(r['id'])+f', temperature: {r["temperature"]}, humidity: {r["humidity"]}, expected_t: {r["temperature"]*100}, expected_h: {r["humidity"]*100}'+' }' for r in rows)+'\n];\n'
    return text,[{**r,'expected_temperature':r['temperature']*100,'expected_humidity':r['humidity']*100} for r in rows]
