"""Diagnose data bindings at an interpreted commit, without inventing validity."""
import json

from .model import Node,Unknown,unwrap
from .relations import Relations


def check_materialization(root,report):
    if not root['quantities']:return
    def inspect():
        connection=unwrap(root['connection'])
        if not isinstance(connection,Node) or connection.name not in {'I2C','SPI'}:
            raise Unknown('finite quantity binding audit currently needs typed I2C/SPI')
        from ..native.prefix import success_prefix
        prefix=success_prefix(root,connection.name.lower())
        available=set(prefix['bound_input_names'])
        relations=Relations(root['quantities'],external_names=available)
        report.add('behavior','satisfied','finite_input_environment',json.dumps({
            'available_inputs':sorted(available),
            'scope':'names bound at one interpreter-checked commit only; no source, numeric validity, native candidate or all-path proof'},sort_keys=True))
        for name,references in relations.references.items():
            missing=sorted(path for path in references if relations.alias(path) is None and path not in available)
            report.add('quantities.'+name,'unknown' if missing else 'satisfied','finite_quantity_input_binding',
                'inputs unavailable at this committed frame/state snapshot: '+', '.join(missing) if missing else
                'referenced inputs have bindings at this finite commit; validity, formula and other paths checked separately')
    report.attempt('behavior','finite_quantity_materialization',inspect)
