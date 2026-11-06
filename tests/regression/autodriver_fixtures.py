"""Host-only synthetic protocol fixtures. Never an input source for real trials."""
import copy
import json
from pathlib import Path

from method.autodriver.implementation.system.protocol.assembler import ROOT, digest


def make_fixture(destination):
    root = Path(destination) / "visible"
    root.mkdir(parents=True, exist_ok=True)
    entries = []

    def add(identifier, path, kind, text, parents=(), role="shared", producer=None):
        content = text.encode("utf8")
        output = root / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        item = dict(id=identifier, path=path, kind=kind, sha256=digest(content),
                    parents=list(parents), os_role=role, approved=True,
                    origin_locator="synthetic protocol fixture; no real device or OS")
        if producer:
            item["producer"] = producer
        entries.append(item)
        return item

    add("format", "method/format.md", "method_specification", (ROOT / "FORMAT_GUIDE.md").read_text())
    add("task", "input/task.json", "public_task_projection", json.dumps({
        "task_id": "synthetic-protocol-demo", "sensor": "ExampleDevice",
        "target_os": "ExampleTarget", "bus": "example-bus",
        "policy": {"mode": "fixed acquisition policy"},
        "requirements": ["Protocol demonstration only; no behavioral validation"],
        "editable_paths": ["target/example.c"],
    }) + "\n")
    add("source_configuration", "input/configuration.json", "source_configuration",
        '{"mode":"fixed acquisition policy","synthetic":true}\n', role="source")
    add("source", "input/source.c", "device_source",
        "/* SYNTHETIC: placeholder for source evidence, not a real driver. */\n"
        "int acquire(void) { return example_acquire(); }\n", role="source")
    add("document", "input/raw-device-document.txt", "device_document",
        "SYNTHETIC DEVICE DOCUMENT\nAn accepted acquisition returns one result.\n")
    add("source_framework", "framework/source_api.txt", "framework_source",
        "SYNTHETIC SOURCE FRAMEWORK\nexample_acquire submits an operation.\n", role="source")
    add("target_framework", "framework/target_api.txt", "framework_source",
        "SYNTHETIC TARGET FRAMEWORK\nAn accepted example request has a completion callback.\n", role="target")
    add("source_fp", "contracts/source.fp", "fp_shared",
        'FrameworkProfile(os="ExampleSource", snapshot="synthetic-only", unresolved=["synthetic capability only"])\n',
        ["source_framework"], "source")
    add("target_fp", "contracts/target.fp", "fp_shared",
        'FrameworkProfile(os="ExampleTarget", snapshot="synthetic-only", unresolved=["synthetic capability only"])\n',
        ["target_framework"], "target")
    add("support", "target/support.txt", "public_target_support",
        "SYNTHETIC INTEGRATION SUPPORT\nEditable driver: target/example.c\n", role="target")
    add("dbc", "contracts/device.dbc", "dbc_candidate",
        'DeviceContract(device="ExampleDevice", connection=Unresolved("synthetic connection"), '
        'selected_policy=FrozenPolicy(mode="fixed acquisition policy"), applicable_conditions={}, '
        'behavior=Unspecified(), unresolved=["format demonstration only"])\n',
        ["source", "source_configuration", "source_fp", "document", "task"])
    add("bp", "contracts/target.bp", "bp_candidate",
        'BindingPlan(device_contract=Ref("dbc"), framework_profile=Ref("target_fp"), '
        'target_support=Ref("support"), scope=Unresolved("synthetic protocol only"), '
        'unresolved=["not behaviorally checked"])\n',
        ["dbc", "target_fp", "support"], "target")
    base = add("code", "target/example.c", "candidate_code",
               "/* SYNTHETIC CANDIDATE */\nint example_driver(void) { return 0; }\n", ["support"], "target")
    add("validation", "checks/binding.json", "internal_diagnostic",
        '{"checker":"synthetic","implemented_check":"fixture formatting only"}\n',
        ["bp"], producer="method_binding_checker")
    add("diagnostic", "checks/candidate.json", "internal_diagnostic",
        '{"subject":"synthetic candidate","issue":"demonstrate a bounded edit"}\n',
        ["code"], producer="method_contract_checker")
    registry = {"schema": "autodriver.context-registry/v0.1", "receipt_id": "synthetic-only",
                "materials": entries}

    def select(*pairs):
        return [dict(role=role, material_id=identifier) for role, identifier in pairs]

    shared = [("method_specification", "format"), ("task_context", "task")]
    parents = [("dbc", "dbc"), ("target_fp", "target_fp"), ("target_support", "support")]
    selections = {
        "fp_build": select(("method_specification", "format"), ("framework_evidence", "target_framework")),
        "dbc_recover": select(*shared, ("source_configuration", "source_configuration"),
                              ("source_evidence", "source"), ("source_fp", "source_fp"),
                              ("document_evidence", "document")),
        "bp_build": select(*shared, *parents),
        "code_generate": select(*shared, *parents, ("bp", "bp"),
                                ("validation_report", "validation"), ("target_code", "code")),
        "repair": select(*shared, *parents, ("bp", "bp"), ("validation_report", "validation"),
                         ("candidate", "code"), ("internal_diagnostic", "diagnostic")),
    }
    requests, responses = {}, {}
    for stage, selected in selections.items():
        request = {
            "schema": "autodriver.llm-call/v0.1", "call_id": "synthetic-" + stage,
            "stage": stage, "condition": "FRAMEWORK" if stage == "fp_build" else "SD",
            "goal": "仅演示调用协议和资料边界，不生成真实器件契约或驱动。",
            # Synthetic rendering allowance only; this changes no real model
            # authorization, token budget or experiment configuration.
            "selections": selected, "max_context_bytes": 131072,
        }
        if stage in {"code_generate", "repair"}:
            request.update(editable_paths=["target/example.c"],
                           base_files={base["path"]: base["sha256"]},
                           host_generation_gate="open", generation_scope="synthetic file-edit protocol only")
        if stage == "repair":
            request["repair_target"] = "code"
        kind = {"fp_build": "fp", "dbc_recover": "dbc", "bp_build": "bp",
                "code_generate": "code", "repair": "code"}[stage]
        if kind == "code":
            artifact = dict(kind="code", format="file_edits", edits=[{
                "path": base["path"], "operation": "replace", "base_sha256": base["sha256"],
                "content": "/* SYNTHETIC EDIT; not a working driver */\nint example_driver(void) { return 1; }\n",
            }])
        else:
            artifact = dict(kind=kind, format="constructor", content={
                "fp": 'FrameworkProfile(os="ExampleTarget", snapshot="synthetic-only", unresolved=["synthetic only"])',
                "dbc": 'DeviceContract(device="ExampleDevice", connection=Unresolved("synthetic connection"), '
                       'selected_policy=FrozenPolicy(mode="fixed acquisition policy"), applicable_conditions={}, '
                       'behavior=Unspecified(), unresolved=["synthetic only"])',
                "bp": 'BindingPlan(device_contract=Ref("dbc"), framework_profile=Ref("target_fp"), '
                      'target_support=Ref("support"), scope=Unresolved("synthetic protocol only"), '
                      'unresolved=["not semantically checked"])',
            }[kind])
        evidence = {"fp": "target_framework", "dbc": "source", "bp": "dbc", "code": "bp"}[kind]
        response = {
            "schema": "autodriver.llm-response/v0.1", "call_id": request["call_id"], "stage": stage,
            "outcome": "candidate", "artifact": artifact,
            "evidence_links": [{"claim": "synthetic protocol demonstration", "material_id": evidence,
                                "start_line": 1, "end_line": 1, "basis": "合成材料的位置引用演示"}],
            "unresolved": [{"subject": "all behavioral semantics", "reason": "synthetic envelope example only",
                            "impact": "required"}],
            "context_requests": [], "change_summary": ["人工构造的协议示例，未调用模型。"],
        }
        requests[stage], responses[stage] = request, response
    return root, registry, requests, responses


def clone(value):
    return copy.deepcopy(value)
