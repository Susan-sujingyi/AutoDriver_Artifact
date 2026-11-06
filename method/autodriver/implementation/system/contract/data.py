"""Device-independent frame integrity and target numerical encoding semantics."""
from dataclasses import dataclass
from fractions import Fraction
from collections.abc import Mapping
from types import MappingProxyType
from collections import Counter

from .model import ContractError, Unknown, Node, node, unwrap
from .expression import scalar, quantity


class DataInvalid(ContractError):
    """Normal runtime data rejection, distinct from a malformed contract."""


def integer(value, minimum=0, maximum=None):
    value = unwrap(value)
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ContractError("integer outside declared range")
    return value


def boolean(value):
    value=unwrap(value)
    if type(value) is not bool:
        raise ContractError("boolean required")
    return value


def byte_sequence(value):
    value=unwrap(value)
    if not isinstance(value, (tuple, list, bytes)) or len(value) > 65536:
        raise ContractError("bounded byte sequence required")
    return bytes(integer(x, 0, 255) for x in value)


def reverse8(value):
    return int(f"{value:08b}"[::-1], 2)


def crc8(data, algorithm):
    algorithm=unwrap(algorithm)
    if not isinstance(algorithm, Node) or algorithm.name != "CRC8":
        raise Unknown("unsupported integrity algorithm")
    polynomial = integer(algorithm["polynomial"], 0, 255)
    current = integer(algorithm["initial"], 0, 255)
    reflected_in = boolean(algorithm["reflect_input"])
    reflected_out = boolean(algorithm["reflect_output"])
    final = integer(algorithm["final_xor"], 0, 255)
    for value in byte_sequence(data):
        current ^= reverse8(value) if reflected_in else value
        for _ in range(8):
            current = ((current << 1) ^ polynomial if current & 0x80 else current << 1) & 0xff
    return (reverse8(current) if reflected_out else current) ^ final


def expand_layout(layout):
    layout = unwrap(layout)
    if layout.name == "FrameLayout":
        return layout
    if layout.name != "CRCProtectedWords":
        raise Unknown("unsupported frame layout")
    encoding = unwrap(layout["encoding"])
    if not isinstance(encoding, Mapping) or set(encoding) != {"width", "signed", "byte_order"}:
        raise ContractError("word encoding requires width/signed/byte_order")
    width = integer(encoding["width"], 1, 4096)
    if width % 8:
        raise Unknown("CRCProtectedWords requires whole-byte words")
    algorithm=unwrap(layout['crc'])
    if algorithm.name != "CRC8":
        raise Unknown("unsupported protected-word integrity")
    fields, checks, offset = {}, [], 0
    for name in layout["names"]:
        if not isinstance(name, str) or not name or name in fields or name + "_crc" in fields:
            raise ContractError("duplicate or invalid protected-word name")
        fields[name] = node("Field", offset=offset*8, **dict(encoding))
        fields[name+"_crc"] = node("Field", offset=offset*8+width, width=8, byte_order="big", signed=False)
        checks.append(node("Checksum", algorithm=algorithm, covered_bytes=((offset, offset+width//8),),
                           expected_field=name+"_crc"))
        offset += width//8 + 1
    return node("FrameLayout", length=offset, fields=MappingProxyType(fields), integrity=tuple(checks), completeness="exact")


def check_layout(layout):
    layout = expand_layout(layout)
    length = integer(layout["length"], 0, 65536)
    if unwrap(layout["completeness"]) != "exact":
        raise Unknown("unsupported completeness rule")
    fields=unwrap(layout['fields'])
    if not isinstance(fields, Mapping):
        raise ContractError("frame fields must be named")
    occupied = set();normalized_fields={};normalized_checks=[]
    for name,field in fields.items():
        field=unwrap(field)
        if not isinstance(field, Node) or field.name != "Field":
            raise ContractError("Field required")
        offset, width = integer(field["offset"]), integer(field["width"], 1, 4096)
        if offset+width > length*8 or occupied.intersection(range(offset, offset+width)):
            raise ContractError("field overlaps or lies outside frame")
        order=unwrap(field['byte_order']);signed=boolean(field['signed'])
        if order not in {"big", "little"}:
            raise ContractError("invalid byte order")
        if order == "little" and (offset % 8 or width % 8):
            raise Unknown("non-byte-aligned little-endian layout needs an explicit definition")
        normalized_fields[name]=node('Field',offset=offset,width=width,byte_order=order,signed=signed)
        occupied.update(range(offset, offset+width))
    for check in unwrap(layout["integrity"]):
        check=unwrap(check)
        if not isinstance(check, Node) or check.name != "Checksum":
            raise Unknown("non-checksum integrity predicate requires evaluation context")
        expected=unwrap(check['expected_field'])
        if expected not in normalized_fields:
            raise ContractError("checksum field is undeclared")
        field = normalized_fields[expected]
        algorithm=unwrap(check['algorithm'])
        if isinstance(algorithm,Node) and algorithm.name=='CRC8' and field['width']!=8:
            raise ContractError('CRC8 requires an eight-bit expected field')
        expected_bytes = set(range(field["offset"]//8, (field["offset"]+field["width"]+7)//8))
        covered = set()
        intervals=[]
        for start, end in unwrap(check["covered_bytes"]):
            start,end=integer(start),integer(end)
            if not start < end <= length or expected_bytes.intersection(range(start, end)) or covered.intersection(range(start,end)):
                raise ContractError("invalid checksum coverage")
            covered.update(range(start,end))
            intervals.append((start,end))
        normalized_checks.append(node('Checksum',algorithm=algorithm,covered_bytes=tuple(intervals),expected_field=expected))
    return node('FrameLayout',length=length,fields=MappingProxyType(normalized_fields),integrity=tuple(normalized_checks),completeness='exact')


@dataclass(frozen=True)
class ValidFrame:
    name: str
    instance: str
    fields: Mapping
    raw: bytes


def decode_frame(layout, data, *, name, instance):
    layout, data = check_layout(layout), byte_sequence(data)
    if len(data) != layout["length"]:
        raise DataInvalid("incomplete frame cannot become valid")
    fields = {}
    all_bits = int.from_bytes(data, "big")
    for key, field in layout["fields"].items():
        offset, width = field["offset"], field["width"]
        if field["byte_order"] == "little":
            value = int.from_bytes(data[offset//8:(offset+width)//8], "little")
        else:
            value = (all_bits >> (len(data)*8-offset-width)) & ((1 << width)-1)
        if field["signed"] and value & (1 << (width-1)):
            value -= 1 << width
        fields[key] = value
    for check in layout["integrity"]:
        covered = b"".join(data[start:end] for start, end in check["covered_bytes"])
        algorithm = check["algorithm"]
        actual = crc8(covered, algorithm) if isinstance(algorithm, Node) else scalar(
            algorithm, {f"byte{i}": b for i, b in enumerate(covered)}, bit_width=8)
        if actual != fields[check["expected_field"]]:
            raise DataInvalid("frame integrity failed; no fields may be committed")
    if not name or not instance:
        raise ContractError("frame requires acquisition identity")
    return ValidFrame(name, instance, MappingProxyType(fields), data)


def commit(frames, required, instance):
    if not required or any(name not in frames for name in required):
        raise ContractError("all required frames must be validated before commit")
    selected = [frames[name] for name in required]
    if any(not isinstance(frame, ValidFrame) or frame.instance != instance for frame in selected):
        raise ContractError("old or unvalidated frame cannot commit a new sample")
    return MappingProxyType({name: frames[name] for name in required})


def frame_status_key(name):
    if not isinstance(name,str) or not name or len(name)>128:raise ContractError('bounded frame identity required')
    return 'frame_status.'+name+'.complete_current'


def frame_environment(frames,instance,variables=None,*,declared_frames=None):
    """Only current validated fields enter guard/calculation evaluation."""
    result=dict(variables or {})
    names=set(frames)|set(declared_frames or ())
    if len(names)>128:raise Unknown('frame completeness environment bound exceeded')
    if names and any(key=='frame_status' or key.startswith('frame_status.') for key in result):
        raise ContractError('frame_status is reserved for observed frame completeness')
    selected=[frame for name,frame in frames.items() if isinstance(frame,ValidFrame) and frame.name==name and frame.instance==instance]
    current={frame.name for frame in selected}
    result.update({frame_status_key(name):name in current for name in names})
    counts=Counter(name for frame in selected for name in frame.fields)
    for frame in selected:
        for name,value in frame.fields.items():
            path=frame.name+'.'+name
            if path in result:raise ContractError('frame field conflicts with an explicit variable: '+path)
            result[path]=value
            if counts[name]==1 and name not in result and name!='frame_status':result[name]=value
    return result


def round_integer(value, mode):
    value = Fraction(value)
    if mode == "floor":
        return value.numerator // value.denominator
    if mode == "ceil":
        return -((-value.numerator) // value.denominator)
    if mode == "toward_zero":
        return int(value)
    if mode == "nearest_ties_even":
        return round(value)
    raise Unknown("unsupported rounding mode")


def power_two(exponent):
    return Fraction(1<<exponent) if exponent>=0 else Fraction(1,1<<-exponent)


def binary_exponent(value):
    value=abs(Fraction(value))
    if not value:raise ContractError('zero has no binary exponent')
    exponent=value.numerator.bit_length()-value.denominator.bit_length()
    return exponent if value>=power_two(exponent) else exponent-1


def round_float(value,storage,mode,overflow='report_gap'):
    """One exact rational -> finite binary32/64 rounding, no binary64 detour."""
    if storage not in {'f32','f64'}:raise Unknown('explicit IEEE binary32/64 encoding required')
    if overflow not in {'report_gap','saturate'}:raise Unknown('explicit finite float overflow policy required')
    value=Fraction(value)
    if max(value.numerator.bit_length(),value.denominator.bit_length())>8192:raise Unknown('float rounding arithmetic bound exceeded')
    precision,emin,emax=(24,-126,127) if storage=='f32' else (53,-1022,1023)
    fraction_bits=precision-1;exponent_bits=8 if storage=='f32' else 11
    exponent=max(binary_exponent(value),emin) if value else emin
    step=power_two(exponent-fraction_bits)
    rounded=round_integer(value/step,mode)*step
    maximum=(2-power_two(-fraction_bits))*power_two(emax)
    if abs(rounded)>maximum:
        if overflow!='saturate':raise ContractError('finite float encoding overflows')
        rounded=maximum if value>=0 else -maximum
    negative=value<0;absolute=abs(rounded)
    if not absolute:encoded_exponent=0;mantissa=0
    elif absolute<power_two(emin):encoded_exponent=0;mantissa=int(absolute/power_two(emin-fraction_bits))
    else:
        current=binary_exponent(absolute);encoded_exponent=current-emin+1
        mantissa=int(absolute/power_two(current-fraction_bits))-(1<<fraction_bits)
    bits=(int(negative)<<(fraction_bits+exponent_bits))|(encoded_exponent<<fraction_bits)|mantissa
    return {'exact_stored':rounded,'stored':-0.0 if negative and not rounded else float(rounded),'stored_bits':bits,'storage_type':storage}


def float_bits_value(bits,storage):
    if storage not in {'f32','f64'}:raise Unknown('explicit native IEEE width required')
    fraction_bits,exponent_bits,bias=(23,8,127) if storage=='f32' else (52,11,1023)
    bits=integer(bits,0,(1<<(fraction_bits+exponent_bits+1))-1)
    exponent=(bits>>fraction_bits)&((1<<exponent_bits)-1);mantissa=bits&((1<<fraction_bits)-1)
    if exponent==(1<<exponent_bits)-1:raise DataInvalid('nonfinite native value cannot publish a physical sample')
    value=mantissa*power_two(1-bias-fraction_bits) if not exponent else ((1<<fraction_bits)+mantissa)*power_two(exponent-bias-fraction_bits)
    return -value if bits>>(fraction_bits+exponent_bits) else value


def encode(physical, physical_unit, encoding, rounding, error_bound):
    if encoding.name != "OutputEncoding" or rounding.name != "Rounding":
        raise ContractError("typed output encoding and rounding required")
    native_unit = quantity(1, encoding["unit"])
    source_unit = quantity(1, physical_unit)
    if native_unit.dimensions != source_unit.dimensions:
        raise ContractError("physical/native unit mismatch")
    physical = Fraction(physical) * source_unit.value / native_unit.value
    scale, offset = Fraction(encoding["scale"]), Fraction(encoding["offset"])
    if not scale:
        raise ContractError("encoding scale cannot be zero")
    storage = encoding["storage_type"]
    floating=storage in {'f32','f64'}
    float_result=round_float((physical-offset)/scale,storage,rounding['mode'],rounding['overflow']) if floating else {}
    if not floating and (not isinstance(storage, str) or storage[:1] not in {"i", "u"} or not storage[1:].isdecimal()):
        raise Unknown("encoding needs an explicit integer storage width")
    stored = float_result.get('exact_stored') if floating else round_integer((physical-offset)/scale, rounding["mode"])
    if not floating:
        bits=integer(int(storage[1:]),1,128)
        lo,hi=(-(1 << (bits-1)), (1 << (bits-1))-1) if storage[0] == 'i' else (0,(1<<bits)-1)
        if not lo<=stored<=hi:
            if rounding['overflow']!='saturate':raise ContractError('encoding exceeds native integer range')
            stored=min(max(stored,lo),hi)
    declared = unwrap(encoding["range"])
    if not isinstance(declared, tuple) or len(declared) != 2:
        raise Unknown("explicit native value range required")
    if not declared[0] <= stored <= declared[1]:
        raise ContractError("encoding exceeds declared legal range")
    error = abs(stored*scale+offset-physical)
    allowed = Fraction(error_bound) * source_unit.value / native_unit.value
    if allowed < 0 or error > allowed:
        raise ContractError("encoding violates physical error bound")
    return {**float_result,"stored": float_result['stored'] if floating else stored, "physical_error": error, "scope": "this input; not a universal numerical proof"}
