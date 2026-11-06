"""Exact arithmetic and dimensional values, interpreted without Python execution."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from fractions import Fraction
from math import isqrt
import operator
import re

from .model import ContractError, Unknown, unwrap


@dataclass(frozen=True)
class Quantity:
    value: Fraction
    dimensions: tuple = ()

    def __init__(self, value, dimensions=()):
        object.__setattr__(self, "value", Fraction(value))
        object.__setattr__(self, "dimensions", tuple(sorted((k, v) for k, v in dict(dimensions).items() if v)))


# Tick is deliberately a different dimension from physical time.
UNITS = {
    "s": (1, "time"), "ms": (Fraction(1, 1000), "time"),
    "us": (Fraction(1, 1000000), "time"), "µs": (Fraction(1, 1000000), "time"),
    "ns": (Fraction(1, 1000000000), "time"), "tick": (1, "tick"),
    "Hz": (1, "frequency"), "V": (1, "voltage"),
    "Pa": (1, "pressure"), "hPa": (100, "pressure"),
    "°C": (1, "temperature_celsius"), "degC": (1, "temperature_celsius"),
    "%RH": (1, "relative_humidity_percent"), "percent_RH": (1, "relative_humidity_percent"), "1": (1, None),
    'lx':(1,'illuminance'),'lux':(1,'illuminance'),
    'ppm':(Fraction(1,1000000),'concentration'),'ppb':(Fraction(1,1000000000),'concentration'),
}
NUMBER_UNIT = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:/\d+)?)\s*([^\s]+)$")


def quantity(value, unit=None):
    unit = unwrap(unit)
    if unit is None:
        if not isinstance(value, str) or not (match := NUMBER_UNIT.fullmatch(value.strip())):
            raise ContractError("explicit quantity and unit required")
        value, unit = match.groups()
    if not isinstance(unit, str):
        raise ContractError("unit must be text or an explicit unresolved value")
    if unit not in UNITS:
        raise Unknown("unsupported unit: " + str(unit))
    scale, dimension = UNITS[unit]
    return Quantity(Fraction(value) * scale, {dimension: 1} if dimension else {})


def seconds(value):
    q = value if isinstance(value, Quantity) else quantity(value)
    if q.dimensions != (("time", 1),) or q.value < 0:
        raise ContractError("nonnegative physical time required")
    return q.value


def _number(value):
    if type(value) in (int, Fraction):
        return Quantity(value)
    if isinstance(value, Quantity):
        return value
    raise ContractError("numeric expression operand required")


def _integer(value):
    q = _number(value)
    if q.dimensions or q.value.denominator != 1:
        raise ContractError("dimensionless integer required")
    return int(q.value)


def _bounded(value):
    q = _number(value)
    if max(abs(q.value.numerator).bit_length(), q.value.denominator.bit_length()) > 8192:
        raise Unknown("exact arithmetic size bound exceeded")
    return q


def check_pure_expression(expression):
    """Validate the declared pure grammar without guessing variable values.

    Missing domains or unsupported numerical proofs remain unknown. An opaque
    source function is not an expression in the closed normative language.
    """
    if not isinstance(expression,str) or not expression or len(expression)>16384:
        raise ContractError('bounded pure expression string required')
    try:tree=ast.parse(expression,mode='eval')
    except (SyntaxError,RecursionError):raise ContractError('physical expression syntax invalid') from None
    if sum(1 for _ in ast.walk(tree))>2048:raise Unknown('pure expression node bound exceeded')
    functions={'min':(1,128),'max':(1,128),'abs':(1,1),'sqrt':(1,1),'exp':(1,1),'ln':(1,1),
               'signed':(2,2),'unsigned':(2,2),'extract':(3,3)}
    operators=(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv,ast.Mod,ast.Pow,
               ast.LShift,ast.RShift,ast.BitAnd,ast.BitOr,ast.BitXor,
               ast.Not,ast.UAdd,ast.USub,ast.Invert,ast.And,ast.Or,
               ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE)
    def visit(value,depth=0):
        if depth>80:raise Unknown('pure expression depth bound exceeded')
        if isinstance(value,ast.Constant):
            if type(value.value) not in {bool,int,float}:raise ContractError('only numeric/boolean expression literals allowed')
        elif isinstance(value,ast.Name):
            if value.id.startswith('_'):raise ContractError('invalid pure expression reference')
        elif isinstance(value,ast.Attribute):
            if value.attr.startswith('_') or not isinstance(value.value,(ast.Name,ast.Attribute)):
                raise ContractError('invalid pure expression field path')
            visit(value.value,depth+1)
        elif isinstance(value,ast.Call):
            if not isinstance(value.func,ast.Name) or value.func.id not in functions or value.keywords:
                raise ContractError('undeclared pure expression function; recover its normative relation as named quantities')
            lo,hi=functions[value.func.id]
            if not lo<=len(value.args)<=hi:raise ContractError('pure expression function arity mismatch')
            for arg in value.args:visit(arg,depth+1)
        elif isinstance(value,(ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare)):
            for child in ast.iter_child_nodes(value):
                if isinstance(child,operators):continue
                if isinstance(child,(ast.operator,ast.unaryop,ast.boolop,ast.cmpop)):
                    raise ContractError('operator is outside the closed pure language')
                visit(child,depth+1)
        else:raise ContractError('expression form is outside the closed pure language')
    visit(tree.body)
    return tree


def bit_guard_width(expression,widths):
    """Use explicit read/field widths for pure bit guards, never C promotion.

    Arithmetic inside a bit operand and mixed widths require an explicit
    representation; a runtime value's magnitude never supplies a width.
    """
    if type(expression) is bool:return None
    if not isinstance(expression,str) or len(expression)>16384:raise Unknown('bounded pure bit guard required')
    try:tree=ast.parse(expression,mode='eval')
    except (SyntaxError,RecursionError):raise Unknown('bit guard syntax is not encoded') from None
    if sum(1 for _ in ast.walk(tree))>2048:raise Unknown('bit guard node bound exceeded')
    operators=(ast.BitAnd,ast.BitOr,ast.BitXor,ast.LShift,ast.RShift)
    nodes=[n for n in ast.walk(tree) if isinstance(n,ast.BinOp) and isinstance(n.op,operators)
           or isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.Invert)]
    if not nodes:return None
    found=set();literals=[]
    def reference(node):
        if isinstance(node,ast.Name):return node.id
        if isinstance(node,ast.Attribute):return reference(node.value)+'.'+node.attr
        raise Unknown('bit guard data reference requires explicit width')
    def visit(node):
        if isinstance(node,(ast.Name,ast.Attribute)):
            key=reference(node)
            if key not in widths:raise Unknown('bit guard data width unavailable: '+key)
            found.add(widths[key])
        elif isinstance(node,ast.Constant) and type(node.value) is int:literals.append(node.value)
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)) and isinstance(node.operand,ast.Constant):
            value=ast.literal_eval(node)
            if type(value) is not int:raise Unknown('integer bit guard literal required')
            literals.append(value)
        elif isinstance(node,ast.BinOp) and isinstance(node.op,(ast.BitAnd,ast.BitOr,ast.RShift)):
            if isinstance(node.op,ast.RShift) and not (isinstance(node.right,ast.Constant) and type(node.right.value) is int):
                raise Unknown('bit guard shift needs an explicit constant representation')
            visit(node.left);visit(node.right)
        else:raise Unknown('bit guard arithmetic/promotion requires an explicit representation')
    for node in nodes:visit(node)
    if len(found)!=1:raise Unknown('bit guard has mixed or undeclared operand widths')
    result=next(iter(found))
    if type(result) is not int or not 1<=result<=4096:raise ContractError('invalid explicit guard data width')
    if any(not -(1<<(result-1))<=value<(1<<result) for value in literals):
        raise Unknown('bit guard constant needs a wider explicit representation')
    if any(isinstance(node,ast.BinOp) and isinstance(node.op,ast.RShift) and not 0<=node.right.value<result for node in nodes):
        raise Unknown('bit guard shift exceeds its explicit data width')
    return result


def evaluate(expression, environment=None, *, bit_width=None):
    """Returns bool or Quantity. Unknown names/unsupported math remain unknown."""
    if type(expression) is bool:
        return expression
    if not isinstance(expression, str) or len(expression) > 16384:
        raise Unknown("expression is not bounded text")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError):
        raise Unknown("expression is not in the supported pure language") from None
    if sum(1 for _ in ast.walk(tree)) > 2048:
        raise Unknown("expression node bound exceeded")
    env = environment or {}
    if bit_width is not None and (type(bit_width) is not int or not 1<=bit_width<=4096):
        raise ContractError('invalid declared bit width')

    def word(value):
        value=_integer(value)
        if bit_width is None:raise Unknown('bit operation requires declared width')
        if not -(1<<(bit_width-1))<=value<(1<<bit_width):raise ContractError('bit operand outside declared width')
        return value

    def path(n):
        if isinstance(n, ast.Name) and not n.id.startswith("_"):
            return n.id
        if isinstance(n, ast.Attribute) and not n.attr.startswith("_"):
            return path(n.value) + "." + n.attr
        raise ContractError("invalid expression reference")

    def compare(left, right, op):
        if type(left) is bool or type(right) is bool:
            if type(left) is not bool or type(right) is not bool or not isinstance(op, (ast.Eq, ast.NotEq)):
                raise ContractError("incompatible boolean comparison")
            return left == right if isinstance(op, ast.Eq) else left != right
        l, r = _number(left), _number(right)
        if l.dimensions != r.dimensions:
            raise ContractError("incompatible comparison dimensions")
        function = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}.get(type(op))
        if function is None:
            raise ContractError("unsupported comparison")
        return function(l.value, r.value)

    def boolean(value):
        if type(value) is not bool:
            raise ContractError("guard must have boolean type")
        return value

    def run(n, depth=0):
        if depth > 80:
            raise Unknown("expression depth bound exceeded")
        child = lambda x: run(x, depth + 1)
        if isinstance(n, ast.Constant):
            if type(n.value) is bool:
                return n.value
            if type(n.value) in (int, float):
                return _bounded(Quantity(Fraction(ast.get_source_segment(expression, n))))
            raise ContractError("only numeric and boolean expression literals are allowed")
        if isinstance(n, (ast.Name, ast.Attribute)):
            name = path(n)
            if name not in env:
                raise Unknown("undefined expression variable: " + name)
            value = env[name]
            return value if type(value) is bool else _bounded(value)
        if isinstance(n, ast.BoolOp):
            # Three-valued short circuit: unknown AND false is false.
            unknown = None
            for v in n.values:
                try:
                    b = boolean(child(v))
                except Unknown as error:
                    unknown = error
                    continue
                if isinstance(n.op, ast.And) and not b:
                    return False
                if isinstance(n.op, ast.Or) and b:
                    return True
            if unknown:
                raise unknown
            return isinstance(n.op, ast.And)
        if isinstance(n, ast.Compare):
            values = [child(n.left)] + [child(x) for x in n.comparators]
            return all(compare(l, r, op) for l, r, op in zip(values, values[1:], n.ops))
        if isinstance(n, ast.UnaryOp):
            v = child(n.operand)
            if isinstance(n.op, ast.Not):
                return not boolean(v)
            q = _number(v)
            if isinstance(n.op, ast.USub):
                return Quantity(-q.value, q.dimensions)
            if isinstance(n.op, ast.UAdd):
                return q
            if isinstance(n.op, ast.Invert):
                if bit_width is None:
                    raise Unknown("bitwise complement requires declared width")
                return Quantity((~word(q)) & ((1 << bit_width) - 1))
        if isinstance(n, ast.BinOp):
            l, r = _number(child(n.left)), _number(child(n.right))
            op = n.op
            if isinstance(op, (ast.Add, ast.Sub, ast.Mod, ast.FloorDiv)):
                if l.dimensions != r.dimensions:
                    raise ContractError("incompatible arithmetic dimensions")
                if isinstance(op, (ast.Mod, ast.FloorDiv)) and r.value == 0:
                    raise ContractError("division by zero")
                if isinstance(op, ast.Add):
                    return _bounded(Quantity(l.value + r.value, l.dimensions))
                if isinstance(op, ast.Sub):
                    return _bounded(Quantity(l.value - r.value, l.dimensions))
                if isinstance(op, ast.Mod):
                    return _bounded(Quantity(l.value % r.value, l.dimensions))
                return Quantity(l.value // r.value)
            if isinstance(op, (ast.Mult, ast.Div)):
                if isinstance(op, ast.Div) and r.value == 0:
                    raise ContractError("division by zero")
                sign = -1 if isinstance(op, ast.Div) else 1
                dims = dict(l.dimensions)
                for k, v in r.dimensions:
                    dims[k] = dims.get(k, 0) + sign * v
                return _bounded(Quantity(l.value / r.value if sign < 0 else l.value * r.value, dims))
            if isinstance(op, ast.Pow):
                power = _integer(r)
                if abs(power) > 64:
                    raise Unknown("power bound exceeded")
                if l.value == 0 and power < 0:
                    raise ContractError("division by zero")
                return _bounded(Quantity(l.value ** power, {k: v * power for k, v in l.dimensions}))
            if isinstance(op, (ast.LShift, ast.RShift, ast.BitAnd, ast.BitOr, ast.BitXor)):
                a, b = _integer(l), _integer(r)
                if bit_width is None or not 1 <= bit_width <= 4096:
                    raise Unknown("bit operation requires declared width")
                a=word(l)
                if not isinstance(op,(ast.LShift,ast.RShift)):b=word(r)
                if isinstance(op, (ast.LShift, ast.RShift)) and not 0 <= b < bit_width:
                    raise ContractError("shift exceeds declared width")
                function = {ast.LShift: operator.lshift, ast.RShift: operator.rshift,
                            ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor}[type(op)]
                value = function(a, b)
                if value < -(1 << (bit_width-1)) or value >= (1 << bit_width):
                    raise ContractError("bit operation overflows declared width")
                return Quantity(value)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            name, args = n.func.id, [child(x) for x in n.args]
            if name in {"min", "max"} and args:
                q = [_number(x) for x in args]
                if len({x.dimensions for x in q}) != 1:
                    raise ContractError("min/max dimension mismatch")
                return (min if name == "min" else max)(q, key=lambda x: x.value)
            if name == "abs" and len(args) == 1:
                q = _number(args[0]); return Quantity(abs(q.value), q.dimensions)
            if name in {"signed", "unsigned", "extract"} and len(args) == (3 if name == "extract" else 2):
                vals = [_integer(x) for x in args]
                a, width = vals[0], vals[-1]
                if not 1 <= width <= 4096:
                    raise ContractError("invalid bit-vector width")
                if name == "extract":
                    offset = vals[1]
                    if offset < 0 or bit_width is None or offset + width > bit_width:
                        raise ContractError("extract outside declared width")
                    return Quantity((word(args[0]) >> offset) & ((1 << width)-1))
                if a < 0 or a >= 1 << width:
                    raise ContractError("bit-vector input outside width")
                return Quantity(a - (1 << width) if name == "signed" and a & (1 << (width-1)) else a)
            if name in {"sqrt", "exp", "ln"} and len(args) == 1:
                q = _number(args[0])
                if name == "sqrt":
                    if q.value < 0 or any(v % 2 for _, v in q.dimensions):
                        raise ContractError("sqrt domain/dimension error")
                    a, b = isqrt(q.value.numerator), isqrt(q.value.denominator)
                    if a*a == q.value.numerator and b*b == q.value.denominator:
                        return Quantity(Fraction(a, b), {k: v//2 for k, v in q.dimensions})
                else:
                    if q.dimensions or (name == "ln" and q.value <= 0):
                        raise ContractError("transcendental domain/dimension error")
                    if (name == "ln" and q.value == 1) or (name == "exp" and q.value == 0):
                        return Quantity(0 if name == "ln" else 1)
                raise Unknown("non-rational real arithmetic needs certified interval support")
        raise ContractError("unsupported or unsafe expression syntax")

    try:
        return run(tree.body)
    except (ZeroDivisionError, OverflowError, RecursionError):
        raise ContractError("arithmetic domain or resource error") from None


def scalar(expression, environment=None, **options):
    value = evaluate(expression, environment, **options)
    q = _number(value)
    if q.dimensions:
        raise ContractError("dimensionless numeric relation required")
    return q.value
