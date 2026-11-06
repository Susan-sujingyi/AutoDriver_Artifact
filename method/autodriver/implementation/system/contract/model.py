"""Bounded data parser and pinned references. Constructor text is never executed."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
from types import MappingProxyType
from collections.abc import Mapping

from ..protocol.language_catalog import check_interfaces, load_catalog


class ContractError(ValueError):
    pass


class Unknown(ContractError):
    """Insufficient evidence or unsupported semantics, rather than a violation."""


class UpstreamGap(Unknown):
    """A checker identified an exact missing prerequisite, not a code defect."""
    def __init__(self, object_, subject, check, detail):
        if object_ not in {'dbc', 'target_fp', 'bp'}:
            raise ValueError('unsupported upstream object')
        super().__init__(detail)
        self.finding = dict(object=object_, subject=subject, check=check,
                            status='unknown', detail=str(detail))


@dataclass(frozen=True)
class Node:
    name: str
    fields: Mapping

    def __getitem__(self, key):
        return self.fields[key]

    def get(self, key, default=None):
        return self.fields.get(key, default)


def node(name, **fields):
    return Node(name, MappingProxyType(fields))


@lru_cache(maxsize=1)
def definitions():
    return {name: ast.parse("def " + entry["signature"] + ":\n pass").body[0].args
            for name, entry in load_catalog().items()}


def parse(content, kind):
    if kind not in {"fp", "dbc", "bp"}:
        raise ContractError("unsupported contract kind")
    if not isinstance(content, str) or len(content.encode()) > 2_000_000:
        raise ContractError("contract text exceeds parser bound")
    try:
        tree = ast.parse(content, mode="eval")
    except (SyntaxError, RecursionError, MemoryError):
        raise ContractError("invalid or excessively nested contract") from None
    if sum(1 for _ in ast.walk(tree)) > 50000:
        raise ContractError("contract node bound exceeded")
    try:
        check_interfaces(tree, kind)
    except ValueError as error:
        raise ContractError(str(error)) from None

    def convert(value, depth=0):
        if depth > 80:
            raise ContractError("contract depth bound exceeded")
        child = lambda x: convert(x, depth + 1)
        if isinstance(value, ast.Constant):
            if type(value.value) not in (str, int, float, bool, type(None)):
                raise ContractError("unsupported literal")
            if isinstance(value.value, float):
                literal=ast.get_source_segment(content,value)
                try:number=Fraction(literal.replace('_',''))
                except (ValueError,OverflowError):raise ContractError('finite exact decimal literal required') from None
                if max(abs(number.numerator).bit_length(),number.denominator.bit_length())>8192:
                    raise ContractError('decimal literal exceeds exact arithmetic bound')
                return number
            return value.value
        if isinstance(value, (ast.List, ast.Tuple)):
            return tuple(child(x) for x in value.elts)
        if isinstance(value, ast.Dict):
            result = {}
            for key, item in zip(value.keys, value.values):
                if key is None:
                    raise ContractError("dictionary expansion is forbidden")
                key = child(key)
                if not isinstance(key, (str, int, tuple)) or key in result:
                    raise ContractError("invalid or duplicate dictionary key")
                result[key] = child(item)
            return MappingProxyType(result)
        if isinstance(value, ast.UnaryOp) and isinstance(value.op, (ast.UAdd, ast.USub)):
            operand = child(value.operand)
            if type(operand) not in (int, Fraction):
                raise ContractError("sign requires a numeric literal")
            return operand if isinstance(value.op, ast.UAdd) else -operand
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            name = value.func.id
            signature = definitions()[name]
            positional = [x.arg for x in signature.posonlyargs + signature.args]
            result = dict(zip(positional, map(child, value.args)))
            result.update({kw.arg: child(kw.value) for kw in value.keywords})
            if signature.vararg:
                result[signature.vararg.arg] = tuple(child(x) for x in value.args[len(positional):])
            for key, default in zip(positional[len(positional)-len(signature.defaults):], signature.defaults):
                if key not in result:
                    result[key] = child(default)
            for key, default in zip(signature.kwonlyargs, signature.kw_defaults):
                if key.arg not in result and default is not None:
                    result[key.arg] = child(default)
            return Node(name, MappingProxyType(result))
        raise ContractError("unsupported constructor syntax")

    try:
        result = convert(tree.body)
    except (KeyError, TypeError, RecursionError):
        raise ContractError("invalid constructor value") from None
    root = {"fp": "FrameworkProfile", "dbc": "DeviceContract", "bp": "BindingPlan"}[kind]
    if not isinstance(result, Node) or result.name != root:
        raise ContractError("incorrect contract root")
    return result


def walk(value, path=""):
    if isinstance(value, Node):
        yield path, value
        for key, item in value.fields.items():
            yield from walk(item, f"{path}.{key}".lstrip("."))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from walk(item, f"{path}.{key}".lstrip("."))
    elif isinstance(value, (tuple, list)):
        for i, item in enumerate(value):
            yield from walk(item, f"{path}.{i}".lstrip("."))


def plain(value):
    if isinstance(value, Node):
        return {"constructor": value.name, "fields": plain(value.fields)}
    if isinstance(value, Mapping):
        # Tuple keys are legal in request error maps; preserve key type.
        return {"entries": [[plain(k), plain(v)] for k, v in value.items()]}
    if isinstance(value, (tuple, list)):
        return [plain(x) for x in value]
    if isinstance(value, Fraction):
        return {"rational": [value.numerator, value.denominator]}
    return value


def identity(value):
    return hashlib.sha256(json.dumps(plain(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def unwrap(value):
    if isinstance(value, Node) and value.name == "Constraint":
        if value["under"] is not None:
            raise Unknown("conditional constraint needs an applicable environment")
        return unwrap(value["value"])
    if isinstance(value, Node) and value.name in {"Unresolved", "Unspecified"}:
        raise Unknown(value["reason"])
    return value


@dataclass(frozen=True)
class Pinned:
    value: object
    sha256: str


class References:
    """Resolve exact declared aliases/material IDs, without any filesystem lookup."""
    def __init__(self, objects, expected):
        self.objects = dict(objects)
        self.expected = dict(expected)

    def resolve(self, reference):
        name = reference["identifier"] if isinstance(reference, Node) and reference.name == "Ref" else reference
        if not isinstance(name, str):
            raise ContractError("reference must be text")
        # Material IDs may contain dots. Prefer an exact ID before path aliases.
        alias, *parts = [name] if name in self.objects else name.split(".")
        pinned = self.objects.get(alias)
        if pinned is None or self.expected.get(alias) != pinned.sha256:
            raise Unknown("missing or changed parent reference: " + alias)
        current = pinned.value
        for part in parts:
            current = unwrap(current)
            if isinstance(current, Node):
                current = current.fields
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            elif isinstance(current, tuple) and part.isdecimal() and int(part) < len(current):
                current = current[int(part)]
            else:
                raise Unknown("unresolved reference path: " + name)
        return unwrap(current)
