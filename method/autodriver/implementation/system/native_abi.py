"""Mechanical public ABI declarations for code, without provider-body fanout.

Roots come from the public interface and the existing approved framework scope.
This supplies exact types, fields and callback signatures, not an implementation
of the future target driver or a new framework/provider semantic guarantee.
"""
import json
from pathlib import PurePosixPath
import re

from .evidence import SourceIndex


def native_abi(workspace, framework_ids, scope_id, *, maximum_lines=2000):
    evidence=workspace.evidence();index=SourceIndex(evidence);parents=[]
    for material in framework_ids:
        entry=workspace.entries[material]
        if (entry['kind']=='framework_source' and entry['os_role']=='target'
                and PurePosixPath(entry['path']).suffix in {'.c','.h','.rs'}):
            index.add(material);parents.append(material)
    interface=workspace.task['target'].get('interface',{})
    requested=[]
    for selector in interface.get('traits',[]):
        terminal=str(selector).rsplit('::',1)[-1].split('<',1)[0]
        if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',terminal):requested.append(terminal)
    requested += [item['structure'] for item in interface.get('native_events',[])
        if isinstance(item,dict) and isinstance(item.get('structure'),str)]
    scope=json.loads(evidence.read(scope_id))
    # Function dependencies can establish which enum/metadata declarations
    # constrain an otherwise untyped integer selector. The projection emits
    # those original declarations, never implementation bodies or a proof.
    requested += scope.get('retrieval',{}).get('roots',[])
    roots=list(dict.fromkeys(requested))
    chunks=index.excerpts(roots,maximum_lines=maximum_lines,declarations_only=True)
    audit=dict(schema='autodriver.public-native-abi-declarations/v1',roots=roots,
        scope_material_id=scope_id,scope_sha256=workspace.entries[scope_id]['sha256'],
        source_materials={material:workspace.entries[material]['sha256'] for material in parents},
        selections=chunks['selections'],gaps=chunks['gaps'],lines=chunks['lines'],
        conditional_declarations=[dict(symbol=unit['symbol'],conditions=index.symbols[unit['symbol']].conditions)
            for unit in chunks['units'] if index.symbols[unit['symbol']].conditions],
        ambiguous_names={name:index.by_name[name] for name in roots if len(index.by_name.get(name,[]))>1},
        assurance='original declaration/type closure only; no provider dispatch or semantic guarantee proved',
        future_driver_implementation='to be generated within editable paths; not an evidence dependency')
    return [dict(item,role='framework_evidence') for item in chunks['selections']],audit
