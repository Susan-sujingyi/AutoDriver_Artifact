"""Local-only transport schemas; these do not validate contract semantics."""
import json
from copy import deepcopy
from pathlib import Path

STAGES = ["fp_build", "dbc_recover", "bp_build", "code_generate", "repair"]
KINDS = ["fp", "dbc", "bp", "code"]
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
IDENTIFIER = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}


def obj(properties, required=()):
    return {"type": "object", "properties": properties,
            "required": list(required), "additionalProperties": False}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


REQUEST = obj({
    "schema": {"const": "autodriver.llm-call/v0.1"},
    "call_id": IDENTIFIER,
    "stage": {"enum": STAGES},
    "condition": {"enum": ["FRAMEWORK", "S", "D", "SD"]},
    "dbc_review": {"type": "boolean"},
    "goal": {"type": "string", "minLength": 1},
    "repair_target": {"enum": KINDS},
    "selections": array(obj({
        "role": {"type": "string"}, "material_id": IDENTIFIER,
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
    }, ["role", "material_id"]), 1),
    "editable_paths": array({"type": "string"}),
    "base_files": {"type": "object", "additionalProperties": HASH},
    "host_generation_gate": {"enum": ["open", "closed"]},
    "generation_scope": {"type": "string"},
    "generation_mode": {"enum": ["with_binding", "without_binding"]},
    "contract_transport": {"enum": ["constructor", "constructor_tree"]},
    "max_context_bytes": {"type": "integer", "minimum": 1},
}, ["schema", "call_id", "stage", "condition", "goal", "selections", "max_context_bytes"])

MATERIAL = obj({
    "id": IDENTIFIER, "path": {"type": "string", "minLength": 1},
    "sha256": HASH, "kind": {"type": "string"},
    "approved": {"type": "boolean"},
    "parents": array(IDENTIFIER),
    "os_role": {"enum": ["source", "target", "shared"]},
    "producer": {"type": "string"},
    "origin_locator": {"type": "string"},
}, ["id", "path", "sha256", "kind", "approved", "parents", "os_role"])
REGISTRY = obj({
    "schema": {"const": "autodriver.context-registry/v0.1"},
    "receipt_id": IDENTIFIER, "materials": array(MATERIAL, 1),
}, ["schema", "receipt_id", "materials"])

EDIT = obj({
    "path": {"type": "string", "minLength": 1},
    "operation": {"enum": ["create", "replace", "delete"]},
    "base_sha256": {"anyOf": [HASH, {"type": "null"}]},
    "content": {"type": ["string", "null"]},
}, ["path", "operation", "base_sha256", "content"])
CONTRACT = obj({
    "kind": {"enum": ["fp", "dbc", "bp"]},
    "format": {"const": "constructor"},
    "content": {"type": "string", "minLength": 1},
}, ["kind", "format", "content"])
CONTRACT_TREE = obj({"kind":{"enum":["fp","dbc","bp"]},"format":{"const":"constructor_tree"},
                     "content":{"type":"object"}},["kind","format","content"])
CONTRACT_EDITS = obj({'kind':{'enum':['fp','dbc','bp']},'format':{'const':'constructor_edits'},
    'base_material_id':IDENTIFIER,'base_sha256':HASH,
    'edits':{'type':'array','minItems':1,'maxItems':32,'items':obj({
        'operation':{'enum':['replace','add','append']},
        'path':{'type':'array','minItems':1,'maxItems':32,'items':{'anyOf':[{'type':'string','minLength':1},{'type':'integer','minimum':0}]}},
        'value':{}},['operation','path','value'])}},['kind','format','base_material_id','base_sha256','edits'])
CODE = obj({
    "kind": {"const": "code"}, "format": {"const": "file_edits"},
    "edits": array(EDIT, 1),
}, ["kind", "format", "edits"])
RESPONSE = obj({
    "schema": {"const": "autodriver.llm-response/v0.1"},
    "call_id": IDENTIFIER, "stage": {"enum": STAGES},
    "outcome": {"enum": ["candidate", "needs_evidence", "blocked"]},
    "artifact": {"anyOf": [CONTRACT, CONTRACT_TREE, CONTRACT_EDITS, CODE, {"type": "null"}]},
    "evidence_links": array({"anyOf":[obj({
        "claim": {"type": "string", "minLength": 1},
        "material_id": IDENTIFIER,
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
        "basis": {"type": "string", "minLength": 1},
    }, ["claim", "material_id", "start_line", "end_line", "basis"]),obj({
        "claim":{"type":"string","minLength":1},"basis":{"type":"string","minLength":1},
        "citations":{"type":"array","items":{"type":"string"},"minItems":1,"maxItems":32}
    },["claim","basis","citations"])]}),
    "unresolved": array(obj({
        "subject": {"type": "string"}, "reason": {"type": "string"},
        "impact": {"enum": ["required", "optional", "unknown"]},
    }, ["subject", "reason", "impact"])),
    "context_requests": array(obj({
        "question": {"type": "string", "minLength": 1},
        "scope": {"enum": ["source", "device_documents", "source_framework", "target_framework", "target_support", "method_specification", "internal_tools", "upstream_stage"]},
        "query": {"type": "string", "minLength": 1},
        "reason": {"type": "string", "minLength": 1},
    }, ["question", "scope", "query", "reason"])),
    "change_summary": array({"type": "string"}),
}, ["schema", "call_id", "stage", "outcome", "artifact", "evidence_links", "unresolved", "context_requests", "change_summary"])
RESPONSE['properties']['context_requests']['maxItems']=8


def outbound_response_schema(request,kind,handles,repair_base=None):
    """One canonical requested carrier, with historical inbound compatibility."""
    schema=deepcopy(RESPONSE);properties=schema['properties']
    properties['call_id']={'const':request['call_id']};properties['stage']={'const':request['stage']}
    artifact=deepcopy(CODE if kind=='code' else CONTRACT_TREE if request.get('contract_transport')=='constructor_tree' else CONTRACT)
    artifact['properties']['kind']={'const':kind}
    properties['artifact']={'anyOf':[artifact,{'type':'null'}]}
    if repair_base is not None:
        edits=deepcopy(CONTRACT_EDITS)
        for key,value in [('kind',kind),('base_material_id',repair_base['material_id']),('base_sha256',repair_base['sha256'])]:
            edits['properties'][key]={'const':value}
        properties['artifact']['anyOf'].insert(0,edits)
    link=deepcopy(RESPONSE['properties']['evidence_links']['items']['anyOf'][1])
    link['properties']['citations']['items']={'enum':sorted(set(handles))}
    properties['evidence_links']['items']=link
    return schema

SCHEMAS = {"request": REQUEST, "registry": REGISTRY, "response": RESPONSE}
for value in SCHEMAS.values():
    value["$schema"] = "https://json-schema.org/draft/2020-12/schema"


if __name__ == "__main__":
    output = Path(__file__).parent / "schemas"
    output.mkdir(exist_ok=True)
    for name, value in SCHEMAS.items():
        (output / f"{name}.schema.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
