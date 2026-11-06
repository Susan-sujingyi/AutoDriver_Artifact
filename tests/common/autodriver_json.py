"""Read method JSON as JSON, keeping exact Unicode from approved sources.

Native task/configuration YAML continues through the package YAML reader.
JSON must not be silently reinterpreted as YAML or sanitized for that parser.
"""
import json
from pathlib import Path

from benchmark.common.api import load as load_yaml
from tests.common.execution.artifacts import read_regular


def load(path):
    path=Path(path)
    if path.suffix=='.json':return json.loads(read_regular(path).decode('utf8'))
    return load_yaml(path)
