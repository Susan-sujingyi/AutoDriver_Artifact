"""Production message assembly and response checks. No network or code apply."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

import jsonschema
import yaml

from .schemas import REGISTRY, REQUEST, RESPONSE,outbound_response_schema
from .language_catalog import check_interfaces
from .prompt_support import ROLE_DESCRIPTIONS, OUTPUT_INSTRUCTIONS, task_brief, output_template

ROOT = Path(__file__).resolve().parent
PROTOCOLS = yaml.safe_load((ROOT / "protocols.yaml").read_text(encoding="utf8"))
MAX_FILE_BYTES = 32 * 1024 * 1024
TASK_FIELDS = {"task_id", "sensor", "source_os", "target_os", "bus", "requirements",
               "native_interface", "editable_paths", "configuration", "policy", "input_condition",
               "scope", "source_selection_receipt", "target_support_receipt"}


class ProtocolError(ValueError):
    pass


class MethodLimitError(ProtocolError):
    def __init__(self,kind,limit,used,message=None):
        super().__init__(message or 'method '+kind+' bound exhausted')
        self.kind=kind;self.limit=limit;self.used=used


class ContextLimitError(MethodLimitError):
    def __init__(self,required_bytes,limit_bytes):
        super().__init__('context_bytes',limit_bytes,required_bytes,'context exceeds budget; no silent truncation is permitted')
        self.required_bytes=required_bytes;self.limit_bytes=limit_bytes


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def schema_check(value, schema):
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as error:
        # Do not echo arbitrary rejected material/output content.
        where = ".".join(map(str, error.absolute_path)) or "root"
        raise ProtocolError(f"invalid protocol field: {where}") from None


def virtual_parts(value):
    if not value or "\\" in value or "\x00" in value or ":" in value:
        raise ProtocolError("invalid virtual path")
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in {"", ".", ".."} for x in value.split("/")):
        raise ProtocolError("path must be normalized and relative")
    if any(x in {".git", ".env"} for x in p.parts):
        raise ProtocolError("private path is not allowed")
    return p.parts


def read_pinned(root, entry):
    """Open each path component relative to a trusted root without symlink traversal."""
    parts = virtual_parts(entry["path"])
    handles = []
    try:
        handles.append(os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for part in parts[:-1]:
            handles.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=handles[-1]))
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=handles[-1])
        handles.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ProtocolError("material is not a bounded regular file")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ProtocolError("material exceeds size limit")
            chunks.append(chunk)
        data = b"".join(chunks)
    except OSError:
        raise ProtocolError(f"material cannot be read safely: {entry['id']}") from None
    finally:
        for fd in reversed(handles):
            os.close(fd)
    if digest(data) != entry["sha256"]:
        raise ProtocolError(f"frozen material changed: {entry['id']}")
    return data


def expected_kind(request):
    if request["stage"] == "repair":
        if "repair_target" not in request:
            raise ProtocolError("repair_target is required")
        return request["repair_target"]
    if "repair_target" in request:
        raise ProtocolError("repair_target is only valid for repair")
    return PROTOCOLS["stages"][request["stage"]]["artifact_kind"]


def required_roles(request):
    stage, condition = request["stage"], request["condition"]
    roles = set(PROTOCOLS["stages"][stage]["required_roles"])
    if stage == "dbc_recover" or (stage == "repair" and request.get("repair_target") == "dbc"):
        roles.add("task_context")
        if condition in {"S", "SD"}:
            roles.update(["source_configuration", "source_evidence"])
        if condition in {"D", "SD"}:
            roles.add("document_evidence")
    if stage == "repair":
        kind = expected_kind(request)
        if kind == "fp":
            roles.add("framework_evidence")
        elif kind == "bp":
            roles.update(["task_context", "dbc", "target_fp", "target_support"])
        elif kind == "code":
            roles.update(["task_context", "dbc", "target_fp", "bp", "target_support", "validation_report"])
    if request.get("generation_mode") == "without_binding":
        if expected_kind(request) != "code":
            raise ProtocolError("direct generation applies only to code objects")
        roles.discard("bp")
    return roles


def query_scopes(request):
    phase = request["stage"]
    if phase == "repair":
        phase = {"fp": "fp_build", "dbc": "dbc_recover", "bp": "bp_build", "code": "code_generate"}[expected_kind(request)]
    scopes = {"method_specification", "internal_tools", "upstream_stage"}
    scopes.update({
        "fp_build": {"source_framework", "target_framework"},
        "dbc_recover": {"source", "device_documents", "source_framework", "target_framework"},
        "bp_build": {"target_framework", "target_support"},
        "code_generate": {"source", "device_documents", "target_framework", "target_support"},
    }[phase])
    if request["condition"] == "D":
        scopes -= {"source", "source_framework"}
    if request["condition"] == "S":
        scopes.discard("device_documents")
    return scopes


def available_tools(request):
    names = list(PROTOCOLS["stages"][request["stage"]]["tools"])
    scopes = query_scopes(request)
    if "source" not in scopes:
        names = [n for n in names if n not in {"resolve_configuration", "slice_device_effects"}]
    if "device_documents" not in scopes:
        names = [n for n in names if n != "retrieve_device_evidence"]
    if request["stage"] == "repair" and expected_kind(request) == "fp":
        names = [n for n in names if n in PROTOCOLS["stages"]["fp_build"]["tools"]]
    return names


def validate_edit_scope(request):
    for path in request.get("editable_paths", []):
        normalized = path[:-1] if path.endswith("/") else path
        if virtual_parts(normalized)[0] != "target":
            raise ProtocolError("edits must remain in target/")
    for path in request.get("base_files", {}):
        if virtual_parts(path)[0] != "target" or not path_allowed(path, request):
            raise ProtocolError("baseline is outside editable scope")


def path_allowed(path, request):
    virtual_parts(path)
    return any(path.startswith(p) if p.endswith("/") else path == p
               for p in request.get("editable_paths", []))


def assemble(visible_root, registry, request, *, executor_specs=None):
    schema_check(registry, REGISTRY)
    schema_check(request, REQUEST)
    stage, condition = request["stage"], request["condition"]
    kind = expected_kind(request)
    if condition == "FRAMEWORK" and not (stage == "fp_build" or (stage == "repair" and kind == "fp")):
        raise ProtocolError("FRAMEWORK condition is limited to FP calls")
    validate_edit_scope(request)
    if kind == "code":
        if request.get("host_generation_gate") != "open" or not request.get("generation_scope"):
            raise ProtocolError("host generation gate/scope is missing or closed")
        if not request.get("editable_paths"):
            raise ProtocolError("code calls need an editable scope")

    entries = {x["id"]: x for x in registry["materials"]}
    if len(entries) != len(registry["materials"]):
        raise ProtocolError("duplicate material identifiers")
    allowed_kinds = set().union(*map(set, PROTOCOLS["role_kinds"].values()))
    derived = set(PROTOCOLS["derived_kinds"])
    checked, visiting, payloads = set(), set(), {}

    def visit(material_id):
        if material_id in visiting:
            raise ProtocolError("cyclic material provenance")
        if material_id in checked:
            return
        if material_id not in entries:
            raise ProtocolError("unknown material parent or selection")
        entry = entries[material_id]
        category = entry["kind"]
        if not entry["approved"] or category not in allowed_kinds:
            raise ProtocolError(f"unapproved material kind: {material_id}")
        if category in derived and not entry["parents"]:
            raise ProtocolError("derived materials require original lineage")
        if category == "internal_diagnostic" and entry.get("producer") not in PROTOCOLS["diagnostic_producers"]:
            raise ProtocolError("diagnostic producer is not permitted")
        if condition == "D" and (category in {"device_source", "source_configuration", "source_analysis"} or entry["os_role"] == "source"):
            raise ProtocolError("D condition forbids source material or source lineage")
        if condition == "S" and category in {"device_document", "document_extract"}:
            raise ProtocolError("S condition forbids device documents or document lineage")
        if stage == "fp_build" or (stage == "repair" and kind == "fp"):
            if category not in {"method_specification", "framework_scope", "framework_source", "framework_document", "framework_index", "fp_shared", "fp_task_local", "internal_diagnostic"}:
                raise ProtocolError("FP lineage must contain framework material only")
        visiting.add(material_id)
        for parent in entry["parents"]:
            visit(parent)
        visiting.remove(material_id)
        data = read_pinned(visible_root, entry)
        if category == "public_task_projection":
            try:
                projection = json.loads(data)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ProtocolError("task projection must be JSON") from None
            if not isinstance(projection, dict) or set(projection) - TASK_FIELDS:
                raise ProtocolError("task projection contains non-public fields")
        payloads[material_id] = data
        checked.add(material_id)

    settings = PROTOCOLS["stages"][stage]
    permitted_roles = set(settings["required_roles"] + settings["optional_roles"])
    selections, actual_roles = [], set()
    citation_handles={}
    seen = set()
    for selected in request["selections"]:
        role, material_id = selected["role"], selected["material_id"]
        if role not in permitted_roles or material_id not in entries:
            raise ProtocolError("unknown stage role or material")
        entry = entries[material_id]
        if entry["kind"] not in PROTOCOLS["role_kinds"][role]:
            raise ProtocolError("material kind does not match its selected role")
        if role == "source_fp" and (condition == "D" or entry["os_role"] != "source"):
            raise ProtocolError("source FP has incompatible condition or role")
        if role == "target_fp" and entry["os_role"] != "target":
            raise ProtocolError("target FP must have target role")
        if role == "candidate":
            candidate_kinds = {"fp": {"fp_shared", "fp_task_local"}, "dbc": {"dbc_candidate"},
                               "bp": {"bp_candidate"}, "code": {"candidate_code"}}
            if entry["kind"] not in candidate_kinds[kind]:
                raise ProtocolError("repair candidate does not match repair_target")
        visit(material_id)
        try:
            lines = payloads[material_id].decode("utf8").splitlines(keepends=True)
        except UnicodeDecodeError:
            raise ProtocolError("binary attachment needs an explicit multimodal adapter") from None
        start, end = selected.get("start_line", 1), selected.get("end_line", len(lines))
        if start > end or end > len(lines):
            raise ProtocolError("invalid excerpt bounds")
        if (role,material_id,start,end) in seen:raise ProtocolError('duplicate context selection')
        seen.add((role,material_id,start,end))
        actual_roles.add(role)
        citation_key=(material_id,entry['sha256'],start,end)
        if citation_key not in citation_handles:citation_handles[citation_key]='c'+str(len(citation_handles)+1)
        from ..contract.support import ALLOWED
        eligible=entry['kind'] not in {'source_index','framework_index','document_index'} and (kind not in ALLOWED or entry['kind'] in ALLOWED[kind])
        selections.append({"role": role, "role_description": ROLE_DESCRIPTIONS[role],
                           "citation_id":citation_handles[citation_key],
                           "evidence_eligible":eligible,
                           "material_id": material_id, "virtual_path": entry["path"],
                           "kind": entry["kind"], "sha256": entry["sha256"],
                           "start_line": start, "end_line": end,
                           "total_lines": len(lines),
                           "origin_locator": entry.get("origin_locator", ""),
                           "text": "".join(lines[start-1:end])})
    if required_roles(request) - actual_roles:
        raise ProtocolError("required context roles are missing")
    if kind == "code":
        selected_bases = {s["virtual_path"]: s["sha256"] for s in selections
                          if s["role"] in {"target_code", "candidate"}
                          and s["start_line"] == 1 and s["end_line"] == s["total_lines"]}
        if any(selected_bases.get(p) != h for p, h in request.get("base_files", {}).items()):
            raise ProtocolError("baseline files need matching visible content")

    common = (ROOT / "templates/common.md").read_bytes()
    phase = (ROOT / "templates" / settings["template"]).read_bytes()
    # Public method-language definitions are instructions, not optional evidence.
    # Do not depend on a caller remembering to select the format guide.
    reference_paths = ["references/response-protocol.md", "references/shared-language.md", "IMPLEMENTATION_PROFILE.md"]
    languages = {kind} if kind in {"fp", "dbc", "bp"} else set()
    material_languages = {"fp_shared": "fp", "fp_task_local": "fp", "dbc_candidate": "dbc", "bp_candidate": "bp"}
    languages.update(material_languages[s["kind"]] for s in selections if s["kind"] in material_languages)
    if "bp" in languages:
        languages.update({"fp", "dbc"})
    if "dbc" in languages:
        languages.add("fp")
    reference_paths.extend(f"references/{language}-language.md" for language in ["fp", "dbc", "bp"] if language in languages)
    references = [(name, (ROOT / name).read_bytes()) for name in reference_paths]
    controls = {k: request[k] for k in ["call_id", "stage", "condition", "goal", "repair_target", "dbc_review", "editable_paths", "base_files", "host_generation_gate", "generation_scope", "generation_mode"] if k in request}
    specs = executor_specs or {}
    if set(specs) - set(available_tools(request)):
        raise ProtocolError("executor is not permitted in this stage")
    from ..contract_patch import repair_base
    patch_base=repair_base(request,kind,selections)
    user_data = {"task_brief": task_brief(request, kind), "call": controls, "expected_artifact_kind": kind,
                 "available_tool_names": sorted(specs), "tool_interfaces": specs,
                 "allowed_context_request_scopes": sorted(query_scopes(request)), "context": selections,
                 "output_template": output_template(request, kind), "output_instructions": OUTPUT_INSTRUCTIONS,
                 "response_schema": outbound_response_schema(request,kind,[item['citation_id'] for item in selections if item['evidence_eligible']],patch_base)}
    if kind=='bp' and user_data['output_template']['artifact']['format']=='constructor_tree':
        # Parent identities are host metadata, not recovered device answers.
        # Pre-fill exact visible read-only parents without inventing a binding.
        for role,field in [('dbc','device_contract'),('target_fp','framework_profile'),('target_support','target_support')]:
            ids=list(dict.fromkeys(item['material_id'] for item in selections if item['role']==role))
            if role=='target_support' and 'integration' in ids:ids=['integration']
            if len(ids)==1:user_data['output_template']['artifact']['content'][field]={'$constructor':'Ref','identifier':ids[0]}
    if patch_base is not None:
        user_data['contract_repair_base']=patch_base
        user_data['output_template']['artifact']={'kind':kind,'format':'constructor_edits',
            'base_material_id':patch_base['material_id'],'base_sha256':patch_base['sha256'],'edits':[]}
    system_content = "\n\n".join([common.decode(), phase.decode(),
                                  "以下是本次使用的自定义输出格式和语言定义，全文已附在消息内；不需要打开其它文件。",
                                  *[content.decode() for _, content in references]])
    if request.get("generation_mode") == "without_binding":
        system_content += ("\n\n本次采用主机固定的直接生成变体：没有目标框架绑定计划（BP），也不要求你输出、补写或假定一份 BP。"
                           "直接根据只读设备行为契约（DBC）、目标框架知识（FP）和公开集成要求生成/修复代码。"
                           "其它资料、输出格式、编辑范围及只读边界保持适用。")
    if request.get('contract_transport')=='constructor_tree' and patch_base is None:
        system_content += ('\n\n本次契约使用v0.1.3 JSON树传输，语义仍为上述同一构造器语言。artifact.format=constructor_tree，content为JSON对象。'
            '每个构造器直接写 {"$constructor":"目录中的名称","参数名":值}，所有实参用目录的名称填写；不需要args/fields包装。'
            '变长参数用目录中的参数名及数组，例如Sequence的steps数组；主机将它转换为同一构造式的变长实参。FrameLayout的fields是该构造器自己的参数，照目录填写。'
            '普通字典和数组保留JSON形式；字符串中的表达式保持原文；只用整数数字，有限十进制字面值写 {"$decimal":"0.01"}，精确有理比例也可用字段允许的字符串。'
            '需要tuple时写{"$tuple":[值]}，非字符串key的字典用{"$map":[[key,value]]}。字符串里的括号/引号只是字符串内容。'
            '主机只做无损传输渲染，然后运行同一目录、类型、引用和语义检查；树格式不改变任何断言或要求。模板只给根类型，须补全目录中全部必需字段。')
    if patch_base is not None:
        system_content += ('\n\n本次契约 repair 优先使用已预填 base_material_id/base_sha256 的 constructor_edits。'
            'edits 是1..32个操作，每项仅含operation、path、value。path为命名字段/字典键/零起始数组索引的数组，例如["quantities","某已声明量","expression"]；它不是文件路径。'
            'replace只替换原候选已有字段/索引，add只添加原候选不存在的字典字段，append在path所指的原有列表末尾追加一个value。'
            '多个append按响应顺序追加，也可同时替换该列表原有元素；原索引不移动。禁止其它重复/父子覆盖写、根替换、删除以及修改$constructor等保留载体键。'
            'value使用同一JSON树格式；表达式仍是完整的封闭纯字符串，不能用源函数名或自然语言代替公式。未编辑字段和值保持原样；只修有据缺口，不能删除约束换通过。'
            'evidence_links、unresolved、context_requests和change_summary仍按本次schema完整填写；不会自动继承缺失的证据或消除未知。'
            '若确需更广修订，可使用response_schema允许的完整constructor_tree/constructor候选；它仍须重过全部检查，不能修改其它父对象。')
    system_content += ('\n\n证据引用使用本次context中给出的citation_id。每项evidence_links写 {"claim":"断言","basis":"原文依据","citations":["实际提供的c编号"]}。'
        '只有evidence_eligible=true的片段可作为本阶段断言的来源；其他片段仅用于理解任务、候选或诊断，不得借其句柄建立事实。'
        '多个片段逐个列出其citation_id，主机只将编号还原为实际提供的material_id/行段，不能补充未提供的行。不要自行合并两个片段的行号。引用存在不证明断言被原文支持。')
    if any(item['kind'] in {'source_index','framework_index','document_index'} for item in selections):
        system_content += ('\n\n本次含显式证据索引：source_index 是机械函数/调用查询及缺口目录，framework_index 是框架声明位置/依赖及查询缺口目录，document_index 是原资料页码/行段目录。各索引的实际范围和未决项由其内容明确声明。'
            '它们不提供设备或框架语义事实，evidence_eligible=false，不能引用索引支持断言。原始资料仍在同一获准池，hash和查询定位保持不变；'
            '先以 needs_evidence 和实际 tool_interfaces 请求具体未决问题所需的完整原文/函数依赖闭包，继续复查守卫、失败路径、图表脚注与事件关联。'
            '全局契约目标和只读父版本不变，不能把索引当成完整证明，不能从未展示的原文补造引用，也不能用局部模型冒充全局完成。')
        system_content += (' read_evidence_range 按实际tool_interfaces中的scope、material_id、start_line、end_line读取同一获准域的完整原文行段，最多6000行。'
            '可按document_index给出的页边界完整读取文本页及相邻脚注；跨页依赖仍须补读。未返回的行、图片内容或未证明语义不能当作已知事实。')
    messages = [{"role": "system", "content": system_content},
                {"role": "user", "content": json.dumps(user_data, ensure_ascii=False, indent=2)}]
    byte_count = len(canonical(messages).encode())
    serialization='readable_json'
    if byte_count > request["max_context_bytes"]:
        # Whitespace is not evidence. Keep every selected field and byte of
        # material text while removing JSON indentation before refusing size.
        messages[1]['content']=canonical(user_data)
        byte_count=len(canonical(messages).encode());serialization='compact_json_lossless'
    if byte_count > request["max_context_bytes"]:
        raise ContextLimitError(byte_count,request['max_context_bytes'])
    audit = {"schema": "autodriver.context-audit/v0.1", "assembly_only": True,
             "call_id": request["call_id"], "stage": stage,"context_serialization":serialization,
             "receipt_id": registry["receipt_id"], "condition": condition,
             "request_sha256": digest(canonical(request).encode()),
             "registry_sha256": digest(canonical(registry).encode()),
             "protocols_sha256": digest((ROOT / "protocols.yaml").read_bytes()),
             "prompt_support_sha256": digest((ROOT / "prompt_support.py").read_bytes()),
             "system_sha256": digest(common), "stage_template_sha256": digest(phase),
             "reference_sha256": {name: digest(content) for name, content in references},
             "prompt_sha256": digest(canonical(messages).encode()), "utf8_bytes": byte_count,
             "selected_ids": [x["material_id"] for x in selections],
             "verified_lineage": {i: entries[i]["sha256"] for i in sorted(checked)}}
    return messages, audit


def check_response(response, request, messages):
    schema_check(request, REQUEST)
    schema_check(response, RESPONSE)
    from ..transport import resolve_citations
    try:response,_=resolve_citations(response,messages)
    except ValueError as error:raise ProtocolError(str(error)) from None
    if isinstance(response.get('artifact'),dict) and response['artifact'].get('format') in {'constructor_tree','constructor_edits'}:
        from ..syntax import normalize_contract_response
        try:response,_=normalize_contract_response(response,messages=messages)
        except ValueError as error:raise ProtocolError(str(error)) from None
    if response["call_id"] != request["call_id"] or response["stage"] != request["stage"]:
        raise ProtocolError("response does not match call identity")
    kind = expected_kind(request)
    artifact = response["artifact"]
    if response["outcome"] == "candidate" and (artifact is None or not response["evidence_links"]):
        raise ProtocolError("candidate needs an artifact and evidence")
    if response["outcome"] == "needs_evidence" and not response["context_requests"]:
        raise ProtocolError("needs_evidence requires a bounded context request")
    if response["outcome"] == "blocked" and not response["unresolved"]:
        raise ProtocolError("blocked response needs an unresolved reason")
    context = json.loads(messages[-1]["content"])["context"]
    by_id = {}
    for selected in context:
        by_id.setdefault(selected["material_id"], []).append(selected)
    for number,link in enumerate(response["evidence_links"]):
        supplied=by_id.get(link["material_id"], [])
        if not any(s.get('evidence_eligible',True) and s['start_line']<=link['start_line']<=link['end_line']<=s['end_line'] for s in supplied):
            if any(s['start_line']<=link['start_line']<=link['end_line']<=s['end_line'] for s in supplied):
                raise ProtocolError('evidence citation is not eligible for this contract stage: '+link['material_id'])
        if not any(s["start_line"] <= link["start_line"] <= link["end_line"] <= s["end_line"]
                   for s in by_id.get(link["material_id"], [])):
            spans=[(s['start_line'],s['end_line']) for s in by_id.get(link['material_id'], [])]
            raise ProtocolError("evidence points outside supplied context: evidence_links["+str(number)+"] material="+
                repr(link['material_id'])+" requested="+str((link['start_line'],link['end_line']))+
                "; supplied intervals="+str(spans)+". Each evidence link must fit one supplied interval; split a claim into separate links when citing disjoint excerpts. Do not cite missing intervening lines.")
    for query in response["context_requests"]:
        if query["scope"] not in query_scopes(request):
            raise ProtocolError("context request is outside current stage or condition")
    if artifact is None:
        return {"transport_valid": True, "semantic_validation": {"status": "unknown", "reason": "no candidate"}}
    if artifact["kind"] != kind:
        raise ProtocolError("response changes the wrong artifact kind")
    if kind != "code":
        try:
            tree = ast.parse(artifact["content"], mode="eval")
        except SyntaxError as error:
            raise ProtocolError("contract expression is not syntactically valid: line "+str(error.lineno)+
                ', column '+str(error.offset)+': '+error.msg) from None
        root = {"fp": "FrameworkProfile", "dbc": "DeviceContract", "bp": "BindingPlan"}[kind]
        if not isinstance(tree.body, ast.Call) or not isinstance(tree.body.func, ast.Name) or tree.body.func.id != root:
            raise ProtocolError("contract has the wrong root constructor")
        allowed = (ast.Expression, ast.Call, ast.Name, ast.Load, ast.Constant, ast.keyword,
                   ast.List, ast.Tuple, ast.Dict, ast.UnaryOp, ast.UAdd, ast.USub)
        for node in ast.walk(tree):
            if not isinstance(node, allowed):
                raise ProtocolError("unsupported expression form")
            if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or not node.func.id[0].isupper()):
                raise ProtocolError("only declarative constructor calls are permitted")
            if isinstance(node, ast.keyword) and node.arg is None:
                raise ProtocolError("keyword unpacking is not permitted")
        try:
            check_interfaces(tree, kind)
        except ValueError as error:
            raise ProtocolError(str(error)) from None
    else:
        touched = set()
        for edit in artifact["edits"]:
            path, operation = edit["path"], edit["operation"]
            if path in touched or not path_allowed(path, request):
                raise ProtocolError("edit is duplicated or outside editable scope")
            touched.add(path)
            baseline = request.get("base_files", {}).get(path)
            if operation == "create":
                if baseline is not None or edit["base_sha256"] is not None or not isinstance(edit["content"], str):
                    raise ProtocolError("invalid create edit")
            elif baseline is None or edit["base_sha256"] != baseline:
                raise ProtocolError("edit baseline is missing or changed")
            elif operation == "delete" and edit["content"] is not None:
                raise ProtocolError("delete content must be null")
            elif operation == "replace" and not isinstance(edit["content"], str):
                raise ProtocolError("replace content must be text")
    if kind == "code":
        semantic = {"status": "unknown", "scope": "file edit structure and baseline only; candidate checks run separately"}
    else:
        from ..contract.check import check_contract,include_response_gaps
        from ..contract.model import References,Pinned,parse,ContractError
        objects={};expected={}
        for selected in context:
            if selected['start_line']!=1 or selected['end_line']!=selected['total_lines']:continue
            value=selected['text'];category=selected['kind']
            try:
                if category in {'fp_shared','fp_task_local','dbc_candidate','bp_candidate'}:
                    value=parse(value,'fp' if category.startswith('fp_') else category.split('_')[0])
                elif category in {'public_task_projection','framework_scope','source_analysis','source_configuration'} or selected['virtual_path'].endswith('.json'):
                    value=yaml.safe_load(value)
            except (ContractError,yaml.YAMLError):
                value=selected['text']  # Malformed candidates remain repairable data.
            identifier=selected['material_id']
            objects[identifier]=Pinned(value,selected['sha256']);expected[identifier]=selected['sha256']
        for alias,role in [('dbc','dbc'),('fp','target_fp'),('task','task_context')]:
            matches=[s['material_id'] for s in context if s['role']==role and s['material_id'] in objects]
            if len(set(matches))==1:objects[alias]=objects[matches[0]];expected[alias]=expected[matches[0]]
        try:
            _, semantic = check_contract(artifact["content"], kind,references=References(objects,expected),
                evidence_claims=[link["claim"] for link in response["evidence_links"]])
            semantic=include_response_gaps(semantic,response['unresolved'])
        except ContractError as error:
            raise ProtocolError(str(error)) from None
    return {"transport_valid": True, "semantic_validation": semantic}


def write_private_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf8") as stream:
        stream.write(value)


def write_private_json(path, value):
    write_private_text(path, json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def readable_messages(messages):
    """Faithful text view of the actual API message contents, with role labels."""
    return "\n\n".join(f"[{m['role'].upper()} MESSAGE]\n\n{m['content']}" for m in messages) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--visible-root", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--response", type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.visible_root.resolve()):
        raise ProtocolError("host output must remain outside the visible material root")
    request = json.loads(args.request.read_text())
    messages, audit = assemble(args.visible_root, json.loads(args.registry.read_text()), request)
    response_result = None
    if args.response:
        response_result = check_response(json.loads(args.response.read_text()), request, messages)
    write_private_json(args.output / "messages.json", messages)
    write_private_text(args.output / "model-input.md", readable_messages(messages))
    write_private_json(args.output / "host-audit.json", audit)
    if response_result:
        write_private_json(args.output / "response-check.json", response_result)
    print(canonical({"assembled": True, "stage": request["stage"], "utf8_bytes": audit["utf8_bytes"],
                     "offline_only": True, "semantic_validation": response_result["semantic_validation"]["status"] if response_result else "not_requested"}))


if __name__ == "__main__":
    main()
