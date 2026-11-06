"""Independent BME280 compensation oracle, based on Bosch datasheet §4.2.3.

No AutoDriver IR, generated contract, source-OS driver, or reference Rust
implementation is imported. Python integers preserve the signed intermediate
arithmetic in the datasheet's 64-bit pressure / integer T and H formulas.
"""
from __future__ import annotations


def trunc_div(numerator: int, denominator: int) -> int:
    """C integer division truncates toward zero, unlike Python // for negatives."""
    if denominator == 0:
        raise ZeroDivisionError("invalid BME280 calibration divisor")
    value = abs(numerator) // abs(denominator)
    return -value if (numerator < 0) != (denominator < 0) else value


def compensate(cal: dict[str, int], raw: dict[str, int]) -> dict[str, int]:
    adc_t, adc_p, adc_h = raw['temperature'], raw['pressure'], raw['humidity']
    t1 = (((adc_t >> 3) - (cal['T1'] << 1)) * cal['T2']) >> 11
    t2 = (((((adc_t >> 4) - cal['T1']) ** 2) >> 12) * cal['T3']) >> 14
    fine = t1 + t2
    temperature = (fine * 5 + 128) >> 8

    p1 = fine - 128000
    p2 = p1 * p1 * cal['P6']
    p2 += (p1 * cal['P5']) << 17
    p2 += cal['P4'] << 35
    p1 = ((p1 * p1 * cal['P3']) >> 8) + ((p1 * cal['P2']) << 12)
    p1 = (((1 << 47) + p1) * cal['P1']) >> 33
    pressure = 1048576 - adc_p
    pressure = trunc_div(((pressure << 31) - p2) * 3125, p1)
    p1 = (cal['P9'] * (pressure >> 13) * (pressure >> 13)) >> 25
    p2 = (cal['P8'] * pressure) >> 19
    pressure = ((pressure + p1 + p2) >> 8) + (cal['P7'] << 4)

    humidity = fine - 76800
    first = (((adc_h << 14) - (cal['H4'] << 20) - cal['H5'] * humidity) + 16384) >> 15
    second = (((((humidity * cal['H6']) >> 10) * (((humidity * cal['H3']) >> 11) + 32768)) >> 10) + 2097152)
    second = (second * cal['H2'] + 8192) >> 14
    humidity = first * second
    humidity -= (((((humidity >> 15) * (humidity >> 15)) >> 7) * cal['H1']) >> 4)
    humidity = max(0, min(humidity, 419430400))
    humidity_q22_10 = humidity >> 12
    return {
        'temperature_centi_c': temperature,
        'pressure_hpa': trunc_div(pressure, 25600),
        'humidity_centi_percent': trunc_div(humidity_q22_10 * 100, 1024),
        't_fine': fine,
        'pressure_q24_8_pa': pressure,
    }


def calibration_registers(cal: dict[str, int]) -> list[int]:
    """Encode a device's NVM coefficients; H4 and H5 are signed 12-bit values."""
    regs = [0] * 256
    fields = ['T1', 'T2', 'T3'] + [f'P{i}' for i in range(1, 10)]
    for i, field in enumerate(fields):
        value = cal[field] & 0xffff
        regs[0x88 + 2 * i] = value & 0xff
        regs[0x89 + 2 * i] = value >> 8
    regs[0xa1] = cal['H1'] & 0xff
    h2 = cal['H2'] & 0xffff
    h4, h5 = cal['H4'] & 0xfff, cal['H5'] & 0xfff
    regs[0xe1:0xe8] = [h2 & 0xff, h2 >> 8, cal['H3'] & 0xff,
                       h4 >> 4, (h4 & 0xf) | ((h5 & 0xf) << 4), h5 >> 4,
                       cal['H6'] & 0xff]
    regs[0xd0] = 0x60
    return regs


def compensate_double(c, raw):
    """Bosch Appendix A, §8.1; output before target-integer quantization."""
    t, p, h = (raw[k] for k in ('temperature', 'pressure', 'humidity'))
    v1 = (t / 16384.0 - c['T1'] / 1024.0) * c['T2']
    v2 = (t / 131072.0 - c['T1'] / 8192.0) ** 2 * c['T3']
    fine = int(v1 + v2)  # the manufacturer's explicit int32_t cast
    temperature = (v1 + v2) / 5120.0
    v1 = fine / 2.0 - 64000.0
    v2 = v1 * v1 * c['P6'] / 32768.0 + v1 * c['P5'] * 2.0
    v2 = v2 / 4.0 + c['P4'] * 65536.0
    v1 = (c['P3'] * v1 * v1 / 524288.0 + c['P2'] * v1) / 524288.0
    v1 = (1.0 + v1 / 32768.0) * c['P1']
    pressure = 0.0 if v1 == 0 else ((1048576.0-p)-v2/4096.0)*6250.0/v1
    if v1 != 0:
        pressure += (c['P9']*pressure*pressure/2147483648.0
                     + pressure*c['P8']/32768.0 + c['P7'])/16.0
    b = fine - 76800.0
    humidity = (h-(c['H4']*64.0+c['H5']/16384.0*b)) * (
        c['H2']/65536.0*(1.0+c['H6']/67108864.0*b*(1.0+c['H3']/67108864.0*b)))
    humidity *= 1.0-c['H1']*humidity/524288.0
    return [temperature*100.0, max(0.0,min(100.0,humidity))*100.0, pressure/100.0]


def pressure_int32(c, raw, fine):
    """Bosch §8.2 alternative, Pa. Chosen vectors stay in documented C widths."""
    v1 = (fine >> 1)-64000
    v2 = (((v1 >> 2)*(v1 >> 2)) >> 11)*c['P6']
    v2 += (v1*c['P5']) << 1
    v2 = (v2 >> 2)+(c['P4'] << 16)
    v1 = (((c['P3']*(((v1 >> 2)*(v1 >> 2)) >> 13)) >> 3)
          + ((c['P2']*v1) >> 1)) >> 18
    v1 = ((32768+v1)*c['P1']) >> 15
    if v1 == 0: return 0
    p = ((1048576-raw['pressure'])-(v2 >> 12))*3125
    p = (p << 1)//v1 if p < 0x80000000 else (p//v1)*2
    v1 = (c['P9']*(((p >> 3)*(p >> 3)) >> 13)) >> 12
    v2 = ((p >> 2)*c['P8']) >> 13
    return p+((v1+v2+c['P7']) >> 4)


def output_envelope(c, raw):
    """Finite union envelope of documented algorithms and integer quantization.

    No sensor-accuracy tolerance: these are deterministic software arithmetic
    checks. Adjacent integer rounding choices are permitted, bounded by one
    output quantum, across §4.2.3, §8.1 and §8.2.
    """
    import math
    fixed = compensate(c, raw)
    integer = [fixed[k] for k in ('temperature_centi_c','humidity_centi_percent','pressure_hpa')]
    double = compensate_double(c, raw)
    pressure32 = pressure_int32(c, raw, fixed['t_fine'])/100.0
    alternatives = [[integer[i], double[i]] for i in range(3)]
    alternatives[2].append(pressure32)
    lower = [math.floor(min(v))-1 for v in alternatives]
    upper = [math.ceil(max(v))+1 for v in alternatives]
    lower[1] = max(0,lower[1]); upper[1] = min(10000,upper[1])
    lower[2] = max(0,lower[2])
    return integer, lower, upper, {'fixed':fixed,'double':double,'pressure32_pa':pressure32*100}


def render_vectors(directory):
    import json
    records, lines = [], []
    for case in json.loads((directory/'cases.yaml').read_text())['vectors']:
        expected, lower, upper, algorithms = output_envelope(case['calibration'],case['raw'])
        raw = [case['raw'][k] for k in ('temperature','pressure','humidity')]
        fields = {'id':case['id'],'registers':calibration_registers(case['calibration']),
                  'raw':raw,'expected':expected,'lower':lower,'upper':upper}
        lines.append('Vector { '+', '.join(k+': '+json.dumps(v) for k,v in fields.items())+' }')
        records.append({**case,'lower':lower,'upper':upper,'documented_algorithms':algorithms})
    return 'const VECTORS: &[Vector] = &[\n'+',\n'.join(lines)+'\n];\n', records


from decimal import Decimal as D, localcontext


def decimal_compensation(c, raw):
    with localcontext() as ctx:
        ctx.prec = 60
        t = D(raw['temperature'])
        delta = t / 131072 - D(c['T1']) / 8192
        tf = (t / 16384 - D(c['T1']) / 1024) * c['T2'] + delta * delta * c['T3']
        fine = D(int(tf))
        v = fine / 2 - 64000
        divisor = (1 + (c['P3'] * v * v / 524288 + c['P2'] * v) / 524288 / 32768) * c['P1']
        offset = (v * v * c['P6'] / 32768 + v * c['P5'] * 2) / 4 + D(c['P4']) * 65536
        p = (1048576 - raw['pressure'] - offset / 4096) * 6250 / divisor
        p += (c['P9'] * p * p / 2147483648 + p * c['P8'] / 32768 + c['P7']) / 16
        v = fine - 76800
        h = (raw['humidity'] - (c['H4'] * 64 + D(c['H5']) / 16384 * v)) * (
            D(c['H2']) / 65536 * (1 + D(c['H6']) / 67108864 * v * (1 + D(c['H3']) / 67108864 * v)))
        h *= 1 - D(c['H1']) * h / 524288
        return [float(tf / 5120 * 100), float(max(D(0), min(D(100), h)) * 100), float(p / 100)]


def _validate(oracle, cases):
    import math
    nominal = cases[0]
    fixed = oracle['compensate'](nominal['calibration'], nominal['raw'])
    # Bosch example T/P coefficients and ADC inputs give 25.08 C, 100653.25 Pa.
    assert fixed['temperature_centi_c'] == 2508
    assert abs(fixed['pressure_q24_8_pa'] / 256 - 100653.25) < .01
    assert oracle['trunc_div'](-7, 3) == -2 and oracle['trunc_div'](7, -3) == -2
    for case in cases:
        expected = decimal_compensation(case['calibration'], case['raw'])
        actual = oracle['compensate_double'](case['calibration'], case['raw'])
        assert all(math.isclose(a, b, abs_tol=1e-9, rel_tol=1e-12) for a, b in zip(expected, actual))
        regs = oracle['calibration_registers'](case['calibration'])
        fields = ['T1', 'T2', 'T3'] + [f'P{i}' for i in range(1, 10)]
        for i, field in enumerate(fields):
            decoded = int.from_bytes(bytes(regs[0x88 + 2*i:0x8a + 2*i]), 'little', signed=field not in ('T1', 'P1'))
            assert decoded == case['calibration'][field]
    # Exercise both packed, signed 12-bit fields over their entire domain.
    for raw in range(4096):
        signed = raw if raw < 2048 else raw - 4096
        c = dict(nominal['calibration'], H4=signed, H5=-1-signed)
        regs = oracle['calibration_registers'](c)
        h4 = (regs[0xe4] << 4) | (regs[0xe5] & 15)
        h5 = (regs[0xe6] << 4) | (regs[0xe5] >> 4)
        assert (h4 - 4096 if h4 & 2048 else h4) == c['H4']
        assert (h5 - 4096 if h5 & 2048 else h5) == c['H5']
    return {'decimal_vectors': len(cases), 'signed_humidity_pairs': 4096,
            'nominal_temperature_centi_c': 2508, 'nominal_pressure_pa': 100653.25}


def validate_vectors():
    import json
    from pathlib import Path
    cases = json.loads((Path(__file__).resolve().parent / 'cases.yaml').read_text())['vectors']
    return _validate(globals(), cases)
