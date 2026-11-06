"""Bounded message assembly for semantic documents and code.

Material authority stays strict. Contract syntax and proof coverage are not
generation prerequisites. No material text is clipped to satisfy the bound.
"""
import json
from pathlib import Path

from .protocol.assembler import (PROTOCOLS, TASK_FIELDS, ProtocolError, ContextLimitError,
                                 canonical, digest, expected_kind, required_roles,
                                 path_allowed, validate_edit_scope)
from .protocol.assembler import available_tools
from .protocol.prompt_support import ROLE_DESCRIPTIONS, output_template
from .protocol.schemas import outbound_response_schema
from .contract.support import ALLOWED
from .semantic_documents import SEMANTIC_GUIDE
from .framework_semantics import semantic_focus
from .document_code import single_source_target


FRAMEWORK_KINDS = {'method_specification','framework_scope','framework_source',
                   'framework_document','framework_index','fp_shared','fp_task_local',
                   'internal_diagnostic'}
INDEX_KINDS = {'source_index','framework_index','document_index'}


def assemble_document(evidence, request, *, executor_specs=None):
    kind = expected_kind(request)
    if type(request['max_context_bytes']) is not int or request['max_context_bytes'] < 1:
        raise ProtocolError('positive host context bound required')
    if request['condition'] not in {'SD','FRAMEWORK'}:
        raise ProtocolError('document method admits SD or framework-only calls')
    if (request['condition'] == 'FRAMEWORK') != (kind == 'fp'):
        raise ProtocolError('framework-only condition and object must agree')
    if kind == 'fp' and request.get('framework_role') not in {'source','target'}:
        raise ProtocolError('explicit framework source/target scope required')
    if kind == 'code':
        validate_edit_scope(request)
        if not request.get('editable_paths'):
            raise ProtocolError('code output needs host-owned editable paths')
    phase = PROTOCOLS['stages'][request['stage']]
    roles = set(phase['required_roles'] + phase['optional_roles'])
    seen, ancestors, context = set(), {}, []

    def visit(material_id, pending=()):
        if material_id in pending:
            raise ProtocolError('cyclic context lineage')
        if material_id in ancestors:
            return
        entry = evidence.registry.get(material_id)
        if entry is None:
            raise ProtocolError('unknown context parent')
        if kind == 'fp':
            if entry['kind'] not in FRAMEWORK_KINDS:
                raise ProtocolError('framework documents cannot contain device/task lineage')
            if entry['os_role'] not in {'shared',request['framework_role']}:
                raise ProtocolError('framework document contains the other OS role')
        for parent in entry['parents']:
            visit(parent, (*pending,material_id))
        # Stage-specific ancestry is checked before reading it. Evidence.read
        # also enforces approved kinds/producers, no symlinks and exact bytes.
        data = evidence.read(material_id)
        if entry['kind'] == 'public_task_projection':
            try:
                task = json.loads(data)
            except ValueError:
                raise ProtocolError('public task projection is not JSON') from None
            if not isinstance(task,dict) or set(task)-TASK_FIELDS:
                raise ProtocolError('non-public task projection fields refused')
        ancestors[material_id] = entry['sha256']

    for selected in request['selections']:
        role, material_id = selected['role'], selected['material_id']
        entry = evidence.registry.get(material_id)
        if role not in roles or entry is None or entry['kind'] not in PROTOCOLS['role_kinds'][role]:
            raise ProtocolError('context stage/role/material authority mismatch')
        if role == 'source_fp' and entry['os_role'] != 'source':
            raise ProtocolError('source FP role mismatch')
        if role == 'target_fp' and entry['os_role'] != 'target':
            raise ProtocolError('target FP role mismatch')
        if role == 'candidate':
            allowed = {'fp':{'fp_shared','fp_task_local'},'dbc':{'dbc_candidate'},
                       'bp':{'bp_candidate'},'code':{'candidate_code'}}[kind]
            if entry['kind'] not in allowed:
                raise ProtocolError('repair candidate has the wrong object kind')
        visit(material_id)
        try:
            lines = evidence.read(material_id).decode('utf8').splitlines(keepends=True)
        except UnicodeDecodeError:
            raise ProtocolError('binary data needs a separate supported adapter') from None
        start, end = selected.get('start_line',1), selected.get('end_line',len(lines))
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise ProtocolError('context excerpt bounds are invalid')
        key = role,material_id,start,end
        if key in seen:
            raise ProtocolError('duplicate context selection')
        seen.add(key)
        context.append(dict(role=role, role_description=ROLE_DESCRIPTIONS[role],
                            material_id=material_id, virtual_path=entry['path'],
                            kind=entry['kind'], os_role=entry['os_role'], sha256=entry['sha256'],
                            start_line=start, end_line=end, total_lines=len(lines),
                            citation_id='c'+str(len(context)+1),
                            evidence_eligible=entry['kind'] not in INDEX_KINDS and
                                (kind not in ALLOWED or entry['kind'] in ALLOWED[kind]),
                            text=''.join(lines[start-1:end])))
    if kind == 'code':
        bases = {item['virtual_path']:item['sha256'] for item in context
                 if item['role']=='target_code' and item['start_line']==1
                 and item['end_line']==item['total_lines']}
        if any(bases.get(path)!=sha or not path_allowed(path,request)
               for path,sha in request.get('base_files',{}).items()):
            raise ProtocolError('code baseline must be fully visible with exact hashes')
    from .tools import SPECS
    if set(executor_specs or {}) - (set(available_tools(request)) & set(SPECS)):
        raise ProtocolError('only real stage-allowed tool interfaces may be advertised')
    controls = {k:v for k,v in request.items() if k!='selections'}
    payload = dict(call=controls, context=context, expected_artifact_kind=kind,
                   purpose={'fp':'Framework profile: OS/API/provider semantics, without device facts.',
                            'dbc':'Device behavior contract: one OS-independent model of this selected device policy.',
                            'bp':'Binding plan: map readonly device semantics onto the target native framework.',
                            'code':'Native target driver implementation from actual contracts and approved originals.'}[kind],
                   missing_context_roles=sorted(required_roles(request)-{s['role'] for s in context}),
                   tool_interfaces=executor_specs or {},
                   authority='Only these approved originals and readonly actual drafts. Indices are locations, not evidence of facts.',
                   generation_policy='advisory_contract_checks')
    focus = semantic_focus(kind, view_only=request.get('view_only') is True)
    payload['semantic_focus'] = focus
    system = SEMANTIC_GUIDE + '\n\n' + focus + '\n\n' + (
        'Host call metadata and parent identities are authoritative. Never change another object or request private material. '
        'Drafts, diagnostics and model claims are not original evidence. Keep genuine conflicts and limitations visible. '
        'For more original evidence in a contract call, optionally return one JSON object with tool_requests: '
        '[{"tool":"an advertised tool name","arguments":{the exact finite arguments}}]. '
        'You may include document text alongside those requests. Without tool requests, readable contract text is accepted. '
        'Do not treat a source location index as a semantic guarantee. No unlisted tool can be executed.')
    definitions = {}
    if kind in {'fp','dbc','bp'}:
        for name in ('shared-language.md',kind+'-language.md'):
            path=Path(__file__).parent/'protocol/references'/name
            text=path.read_text()
            definitions[name]=digest(text.encode())
            system += ('\n\nOptional executable-view notation, used only by finite method checkers. '
                       'You may include one complete derived constructor view in a fenced '
                       'autodriver-projection block, or a JSON executable_projection string alongside '
                       'your primary readable document. Unsupported parts stay explicit. No projection '
                       'or syntax failure prevents code generation; do not spend a response fixing format alone.\n'+text)
    if any(item['kind'] in {'fp_shared','fp_task_local'} and 'FrameworkProfile(' in item['text']
           for item in context):
        for name in ('shared-language.md','fp-language.md'):
            if name in definitions:
                continue
            path = Path(__file__).parent/'protocol/references'/name
            text = path.read_text()
            definitions[name] = digest(text.encode())
            system += '\n\nOptional legacy notation in an existing readonly FP; new output need not use it:\n'+text
    if kind == 'code':
        payload['response_schema'] = outbound_response_schema(
            request,kind,[s['citation_id'] for s in context if s['evidence_eligible']])
        scopes={'internal_tools'}
        for spec in (executor_specs or {}).values():
            scopes.update(spec.get('properties',{}).get('scope',{}).get('enum',[]))
        payload['response_schema']['properties']['context_requests']['items']['properties']['scope']['enum']=sorted(scopes)
        payload['output_template'] = output_template(request,kind)
        payload['implementation_boundary']=dict(
            existing_editable_files=request.get('base_files',{}),
            missing_exact_editable_files=[path for path in request['editable_paths']
                if path not in request.get('base_files',{}) and not path.endswith('/') and not any(c in path for c in '*?[')],
            future_driver='Generate its fields, private state and method bodies; public integration specifies what to supply.',
            framework_abi='Use original framework declarations and typed finite retrieval; no existing target driver body or hidden reference is needed.')
        source_target=single_source_target(request)
        if source_target is not None:
            payload['preferred_code_carrier']=dict(target=source_target,
                output='One complete '+source_target['language']+' source fence, with no surrounding prose or JSON.',
                metadata='Host supplies the immutable path, operation and original baseline hash. Source bytes remain unchanged.',
                alternatives='Use response_schema JSON for evidence requests or cumulative file_edits.')
            system += ('\nFor this single exact editable source file, prefer one complete fenced '+source_target['language']+
                ' source block with a newline before its closing fence. Return the complete cumulative implementation; '
                'the host supplies its path, operation and original baseline hash. Do not JSON-escape source code. '
                'The supplied response_schema JSON remains available for evidence requests or file_edits. ')
        system += ('\nFor JSON code output, use the supplied response_schema and cumulative file_edits. '
                   'Preserve the public native ABI and allowed paths. A missing baseline file uses create and null base hash. '
                   'Parent contracts are actual drafts with advisory findings, not verified guarantees. '
                   'Implement from their meanings and approved originals. No independent evaluator feedback is available. '
                   'An empty base_files map means the editable implementation must be newly created; it is not a request '
                   'for an existing target driver. Choose private fields, request states and method bodies that implement '
                   'the contracts. Public integration calls describe your deliverable. '
                   'For evidence continuations use response_schema.context_requests, not top-level tool_requests. '
                   'Its query is a JSON string containing only an advertised tool and exact arguments. The outer scope '
                   'must equal arguments.scope, or be internal_tools; include question/reason. Unknown future-driver '
                   'bodies are not framework facts to retrieve. Never request hidden references or device-specific target examples.')
    else:
        payload['recommended_output'] = {'document':'actual semantic draft, any readable format',
                                          'executable_projection':'optional single derived view in the supplied notation; not another normative contract',
                                          'tool_requests':'optional bounded requests; no mandatory constructor syntax'}
    if request.get('view_only'):
        payload['purpose']='Compile a derived executable view of the readonly primary semantic document.'
        payload['recommended_output']={'executable_projection':'one complete constructor expression in the supplied notation'}
        system += ('\nThis is a representation-only compiler call, not primary contract repair. '
                   'The candidate is the readonly primary document. Preserve its exact semantic scope and explicit gaps; '
                   'do not infer new device facts, policies or guarantees, and do not recheck original evidence. '
                   'Compile its one global control/data model into the supplied notation. Represent unsupported or '
                   'unstated facts with Unspecified/Unresolved. For BP, readonly DBC/FP and any derived DBC view '
                   'provide reference names, not authority to change primary facts. Return only a complete constructor '
                   'expression or a JSON object with executable_projection as that expression. No tools are available. '
                   'The primary document will not change. Unsupported output remains an unavailable view and never blocks code or submission.')
        system += ('\nThe current executable FSM supports bool/int/real variables with explicit literal initial values. '
                   'Do not invent enum, frame, sample or identity declaration types, or initialize a genuine unknown to a guessed value. '
                   'Use existing lifecycle/current-frame/commit semantics where they already express the primary meaning; '
                   'keep unrepresentable facts explicit as gaps rather than changing guards, inventing progress or a recovery policy. '
                   'For FP preserve the actual established API preconditions and effects in the supported FP notation.')
    messages = [{'role':'system','content':system}, {'role':'user','content':canonical(payload)}]
    byte_count = len(canonical(messages).encode())
    if byte_count > request['max_context_bytes']:
        raise ContextLimitError(byte_count,request['max_context_bytes'])
    audit = dict(schema='autodriver.document-context-audit/v1',call_id=request['call_id'],stage=request['stage'],
                 prompt_sha256=digest(canonical(messages).encode()), bytes=byte_count,
                 max_context_bytes=request['max_context_bytes'],request_sha256=digest(canonical(request).encode()),
                 original_lineage=ancestors, context=[{k:v for k,v in s.items() if k!='text'} for s in context],
                 method_definition_files=definitions,
                 semantic_focus_sha256=digest(focus.encode()),
                 missing_context_roles=payload['missing_context_roles'],
                 contract_assurance='not_asserted', source_or_candidate_text_clipped=False)
    return messages,audit
