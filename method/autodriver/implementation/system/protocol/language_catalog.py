"""Derive constructor interfaces from public language docs; never execute them."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LANGUAGES = ("shared", "fp", "dbc", "bp")
HEADER = re.compile(r"^### ([A-Z][A-Za-z0-9_]*\(.*\))$")


def load_catalog(root=ROOT):
    entries = {}
    for language in LANGUAGES:
        relative = f"references/{language}-language.md"
        for number, line in enumerate((root / relative).read_text(encoding="utf8").splitlines(), 1):
            match = HEADER.fullmatch(line)
            if not match:
                continue
            signature = match[1]
            definition = ast.parse("def " + signature + ":\n    pass\n").body[0]
            name, args = definition.name, definition.args
            if name in entries:
                raise ValueError("duplicate public constructor definition")
            positional = [arg.arg for arg in args.posonlyargs + args.args]
            required = positional[:len(positional) - len(args.defaults)]
            required += [a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults) if default is None]
            entries[name] = dict(name=name, language=language, signature=signature,
                                 positional=positional, keyword_only=[a.arg for a in args.kwonlyargs],
                                 required=required, varargs=args.vararg.arg if args.vararg else None,
                                 varkw=args.kwarg.arg if args.kwarg else None,
                                 document=relative, line=number)
    return entries


def check_interfaces(tree, kind, catalog=None):
    """Check declaration names/argument binding, not value types or behavior."""
    entries = load_catalog() if catalog is None else catalog
    callable_names = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value is Ellipsis:
            raise ValueError("documentation placeholders are not contract values")
        if isinstance(node, ast.Name) and id(node) not in callable_names:
            raise ValueError("bare symbols are not contract values")
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            raise ValueError("constructor must be a declared name")
        entry = entries.get(node.func.id)
        if entry is None or entry["language"] not in {"shared", kind}:
            raise ValueError("unknown constructor or wrong contract layer: "+node.func.id+" in "+kind)
        positional = entry["positional"]
        if len(node.args) > len(positional) and entry["varargs"] is None:
            raise ValueError("too many constructor arguments")
        supplied = set(positional[:len(node.args)])
        for keyword in node.keywords:
            if keyword.arg is None or keyword.arg in supplied:
                raise ValueError("unpacking or duplicate constructor arguments")
            if keyword.arg not in positional + entry["keyword_only"] and entry["varkw"] is None:
                raise ValueError("unknown constructor field: "+node.func.id+"."+keyword.arg+"; allowed: "+", ".join(positional+entry["keyword_only"]))
            supplied.add(keyword.arg)
        if set(entry["required"]) - supplied:
            raise ValueError("required constructor fields are missing: "+node.func.id+" "+", ".join(sorted(set(entry["required"])-supplied)))


def index_document():
    entries = load_catalog()
    return {"schema": "autodriver.primitive-index/v0.1",
            "counts": {language: sum(e["language"] == language for e in entries.values()) for language in LANGUAGES},
            "constructors": {name: entries[name] for name in sorted(entries)}}


if __name__ == "__main__":
    output = ROOT / "references/primitive-index.json"
    output.write_text(json.dumps(index_document(), ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(index_document()["counts"]))
