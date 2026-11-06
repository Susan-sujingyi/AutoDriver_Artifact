"""Five-call transport with faithful inputs, bounded evidence recovery and checks."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time

from .protocol.assembler import assemble,check_response,readable_messages,write_private_json,write_private_text,expected_kind,ProtocolError,MethodLimitError,ContextLimitError,schema_check,digest,canonical
from .protocol.schemas import RESPONSE
from .tools import ToolExecutor


def decimal_carrier(response,json_text,raw,audit):
    """Preserve decimal tokens from raw JSON; never reconstruct a float."""
    artifact=response.get('artifact') if isinstance(response,dict) else None
    if (not isinstance(artifact,dict) or artifact.get('kind') not in {'fp','dbc','bp'}
            or artifact.get('format') not in {'constructor_tree','constructor_edits'}):
        return response,audit
    class DecimalToken(str):pass
    precise=json.loads(json_text,parse_float=DecimalToken)['artifact']
    literals=[]
    def convert(value,path,depth=0):
        if depth>80:raise ProtocolError('exact decimal carrier depth bound exceeded')
        if isinstance(value,DecimalToken):
            if len(value)>512 or len(literals)>=50000:raise ProtocolError('exact decimal carrier literal bound exceeded')
            literals.append({'path':path,'literal':str(value)})
            return {'$decimal':str(value)}
        if isinstance(value,list):return [convert(item,path+[i],depth+1) for i,item in enumerate(value)]
        if isinstance(value,dict):return {key:convert(item,path+[key],depth+1) for key,item in value.items()}
        return value
    normalized=deepcopy(response)
    if artifact['format']=='constructor_tree':
        normalized['artifact']['content']=convert(precise.get('content'),['artifact','content'])
    elif isinstance(precise.get('edits'),list):
        for i,edit in enumerate(precise['edits']):
            if isinstance(edit,dict) and 'value' in edit:
                normalized['artifact']['edits'][i]['value']=convert(edit['value'],['artifact','edits',i,'value'])
    if not literals:return response,audit
    return normalized,{'policy':'json-exact-decimal-carrier/v1','original_sha256':digest(raw.encode()),
        'normalized_sha256':digest(canonical(normalized).encode()),'decimal_literals':literals,'envelope_normalization':audit,
        'scope':'raw JSON decimal tokens represented as explicit exact decimal literals; no arithmetic, value inference, binary64 reconstruction or contract repair'}


def decode_response(raw,*,exact_decimals=True):
    """Remove only JSON trailing commas outside strings, with an explicit audit.

    No keys, values, escapes, prose or constructor text are repaired here.
    Duplicate keys and all other malformed envelopes remain errors.
    """
    def object_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ProtocolError('duplicate response JSON key: '+key)
            result[key]=value
        return result
    try:
        response=json.loads(raw,object_pairs_hook=object_pairs)
        return decimal_carrier(response,raw,raw,None) if exact_decimals else (response,None)
    except json.JSONDecodeError as error:initial_error=error
    removed=[];in_string=False;escape=False
    for i,char in enumerate(raw):
        if in_string:
            if escape:escape=False
            elif char=='\\':escape=True
            elif char=='"':in_string=False
            continue
        if char=='"':in_string=True
        elif char==',':
            j=i+1
            while j<len(raw) and raw[j].isspace():j+=1
            if j<len(raw) and raw[j] in ']}':removed.append(i)
    def location(error):
        return 'model response is not one JSON object: line '+str(error.lineno)+', column '+str(error.colno)+', offset '+str(error.pos)+': '+error.msg
    if not removed:raise ProtocolError(location(initial_error))
    selected=set(removed);normalized=''.join(char for i,char in enumerate(raw) if i not in selected)
    try:response=json.loads(normalized,object_pairs_hook=object_pairs)
    except json.JSONDecodeError as error:raise ProtocolError(location(error)+' after trailing-comma normalization') from None
    audit={'policy':'json-trailing-comma-only/v1','original_sha256':digest(raw.encode()),
                     'normalized_sha256':digest(normalized.encode()),'removed_character_offsets':removed,
                     'scope':'envelope syntax only; values and constructor strings unchanged; original response retained'}
    return decimal_carrier(response,normalized,raw,audit) if exact_decimals else (response,audit)


def merge_selections(selections, additions, evidence):
    """Union overlapping/adjacent intervals without inventing missing context."""
    groups={};totals={}
    for selected in selections+additions:
        key=selected['role'],selected['material_id']
        if selected['material_id'] not in totals:
            totals[selected['material_id']]=len(evidence.read(selected['material_id']).decode('utf8').splitlines())
        total=totals[selected['material_id']]
        start,end=selected.get('start_line',1),selected.get('end_line',total)
        groups.setdefault(key,[]).append((start,end))
    result=[]
    for (role,id),spans in groups.items():
        merged=[]
        for start,end in sorted(spans):
            if merged and start<=merged[-1][1]+1:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
            else:merged.append((start,end))
        result.extend(dict(role=role,material_id=id,start_line=a,end_line=b) for a,b in merged)
    return result


class RejectedResponse(ProtocolError):
    def __init__(self,response,reason,*,category='response_protocol',diagnostic=None):
        super().__init__(reason)
        self.response=response
        self.category=category;self.diagnostic=diagnostic


class RejectedEnvelope(ProtocolError):
    """Opaque transport text; no fields or contract facts are inferred."""
    def __init__(self,raw,reason):
        super().__init__(reason);self.raw=raw


def source_query_batches(queries,specifications):
    """Union complete source closures, without merging evidence or semantics.

    Each original query remains archived. A single analysis over the union of
    roots contains every requested function/callee graph, avoiding repeated
    copies of the same translation-unit analysis in the model context.
    """
    groups=[];other=[]
    for index,query in enumerate(queries):
        try:
            if query['scope']!='internal_tools':raise ValueError('different executor')
            command=json.loads(query['query'])
            if set(command)!={'tool','arguments'} or command['tool']!='slice_device_effects':raise ValueError('different executor')
            schema_check(command['arguments'],specifications['slice_device_effects'])
            roots=command['arguments']['roots']
        except (ValueError,KeyError,TypeError):
            other.append((query,[index]));continue
        if groups and len(set(groups[-1][0]+roots))<=32:
            groups[-1][0][:]=list(dict.fromkeys(groups[-1][0]+roots));groups[-1][1].append(index)
        else:groups.append((list(dict.fromkeys(roots)),[index]))
    for roots,members in groups:
        if len(members)==1:other.append((queries[members[0]],members));continue
        other.append(({'scope':'internal_tools','query':json.dumps({'tool':'slice_device_effects','arguments':{'roots':roots}}),
            'question':'Recover the union of these complete requested source function/callee closures.',
            'reason':'Identical frozen source/configuration executor; original questions and root lists retained in the response and retrieval audit.'},members))
    return sorted(other,key=lambda item:item[1][0])


class CallRunner:
    def __init__(self,evidence,output,*,client=None,maximum_calls=64,maximum_refinements=3,maximum_repairs=2,variant='full',configuration=None,material_writer=None):
        if not 1<=maximum_calls<=512 or not 0<=maximum_refinements<=8:
            raise ValueError('bounded call and refinement limits required')
        self.evidence=evidence
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=True)
        self.client=client
        self.maximum_calls=maximum_calls
        self.maximum_refinements=maximum_refinements
        self.calls=[]
        self.call_ids=set()
        self.variant=variant
        self.dbc_candidate_seen=False
        self.configuration=configuration
        if not 0<=maximum_repairs<=8:raise ValueError('bounded object repair limit required')
        self.maximum_repairs=maximum_repairs
        self.material_writer=material_writer
        self.last_request=None
        self.last_messages=None
        self.saved_responses={}
        self.prior_code_repairs=0
        self.framework_replacements={}

    def call(self,request,*,saved_response=None,protected_materials=()):
        if saved_response is None and self.saved_responses:
            if request['call_id'] not in self.saved_responses:
                raise ProtocolError('saved response call order changed; refuse network fallback')
            saved_response=self.saved_responses.pop(request['call_id'])
        if len(self.calls)>=self.maximum_calls:raise MethodLimitError('logical_calls',self.maximum_calls,len(self.calls))
        if request['stage']=='repair' and request.get('repair_target')=='code':
            used=self.prior_code_repairs+sum(call.get('stage')=='repair' and call.get('repair_target')=='code' for call in self.calls)
            if used>=4:raise MethodLimitError('code_repairs',4,used,'cumulative four code repair requests exhausted, including evidence/format continuations')
        if request['call_id'] in self.call_ids:raise ProtocolError('call ID cannot be reused; replay requires a saved host receipt')
        executor=ToolExecutor(self.evidence,request,configuration=self.configuration)
        registry={'schema':'autodriver.context-registry/v0.1','receipt_id':'method-evidence-registry',
                  'materials':list(self.evidence.registry.values())}
        from .variants import admit_call
        admit_call(self.variant,request,registry,dbc_candidate_seen=self.dbc_candidate_seen)
        scope_changes=[]
        for scope_attempt in range(4):
            try:
                messages,audit=assemble(self.evidence.root,registry,request,executor_specs=executor.specs())
                break
            except ContextLimitError:
                if scope_attempt==3:raise
                from .context_scope import reselect
                revised=reselect(request,self.evidence,self.register,scope_attempt+1,protected_materials=protected_materials)
                if revised is None:raise
                request,change=revised;scope_changes.append(change)
                registry['materials']=list(self.evidence.registry.values())
                executor=ToolExecutor(self.evidence,request,configuration=self.configuration)
                admit_call(self.variant,request,registry,dbc_candidate_seen=self.dbc_candidate_seen)
        if saved_response is not None:
            if (saved_response.get('prompt_sha256')!=audit['prompt_sha256']
                    or saved_response.get('response_sha256')!=digest(saved_response.get('text','').encode())
                    or not saved_response.get('origin_request_id')):
                raise ProtocolError('saved response identity changed; refuse network fallback')
        call_id=request['call_id'];prefix=self.output/call_id
        if any(self.output.glob(call_id+'.*')):raise ProtocolError('saved call exists; refuse implicit replay or fresh billing')
        self.call_ids.add(call_id)
        self.last_request=deepcopy(request)
        self.last_messages=deepcopy(messages)
        write_private_json(str(prefix)+'.request.json',request)
        write_private_json(str(prefix)+'.messages.json',messages)
        write_private_text(str(prefix)+'.model-input.md',readable_messages(messages))
        write_private_json(str(prefix)+'.audit.json',audit)
        if scope_changes:
            write_private_json(str(prefix)+'.scope-reselection.json',{'steps':scope_changes,
                'max_context_bytes_unchanged':request['max_context_bytes'],'new_upstream_requests_from_planning':0,
                'protected_retrieved_materials':sorted(protected_materials)})
        started=time.time();record={'call_id':call_id,'stage':request['stage'],'repair_target':request.get('repair_target'),
                                  'started':started,'status':'started','prompt_sha256':audit['prompt_sha256']}
        record['execution_mode']='saved_response_local_validation' if saved_response is not None else 'client_transport'
        if saved_response is not None:record['origin_request_id']=saved_response['origin_request_id']
        self.calls.append(record)
        write_private_json(str(prefix)+'.state.json',record)
        try:
            if saved_response is not None:
                raw=saved_response['text']
            else:
                client=self.client
                if client is None:
                    from method.common.client import complete
                    client=complete
                raw=client(messages[1]['content'],system=messages[0]['content'],stage=request['stage'],
                           step=call_id+(':'+request['repair_target'] if 'repair_target' in request else ''))
            if not isinstance(raw,str):raise ProtocolError('model transport must return text')
            write_private_text(str(prefix)+'.response.txt',raw)
            try:
                response,normalization=decode_response(raw)
                if not isinstance(response,dict):raise ProtocolError('response envelope must be one JSON object')
            except ProtocolError as error:
                if len(raw.encode())>2_000_000:raise
                write_private_json(str(prefix)+'.checks.json',{'transport_valid':False,'reason':str(error),'raw_sha256':digest(raw.encode())})
                raise RejectedEnvelope(raw,str(error)) from None
            if normalization is not None:write_private_json(str(prefix)+'.envelope-normalization.json',normalization)
            from .syntax import normalize_contract_response
            from .transport import resolve_citations
            from .contract.model import ContractError
            try:
                response,citation_normalization=resolve_citations(response,messages)
            except ContractError as error:
                # An evidence request may have no artifact at all. Preserve its
                # rejected text without promoting it to a contract candidate;
                # the same bounded envelope-repair path handles bad citations.
                artifact=response.get('artifact')
                if (response.get('call_id')!=call_id or response.get('stage')!=request['stage']
                        or artifact is not None and (not isinstance(artifact,dict)
                            or artifact.get('kind')!=expected_kind(request))):
                    raise ProtocolError('citation rejection belongs to a different call, stage or artifact kind') from None
                if len(raw.encode())>2_000_000:raise ProtocolError('rejected response exceeds bounded repair size') from None
                write_private_json(str(prefix)+'.checks.json',{
                    'transport_valid':False,'reason':str(error),'raw_sha256':digest(raw.encode())})
                raise RejectedEnvelope(raw,str(error)) from None
            if citation_normalization is not None:write_private_json(str(prefix)+'.citation-normalization.json',citation_normalization)
            try:
                response,constructor_normalization=normalize_contract_response(response,messages=messages)
            except ValueError as error:
                if normalization and normalization.get('policy')=='json-exact-decimal-carrier/v1':
                    # Refused edits remain the original untrusted JSON in a
                    # repair diagnostic. This data is never interpreted as a
                    # contract; preserve older exact diagnostic/prompt bytes.
                    original,_=decode_response(raw,exact_decimals=False)
                    response=deepcopy(response);response['artifact']=original['artifact']
                try:schema_check(response,RESPONSE)
                except ProtocolError as schema_error:
                    if len(raw.encode())>2_000_000:raise
                    artifact=response.get('artifact')
                    if (response.get('call_id')!=call_id or response.get('stage')!=request['stage']
                            or not isinstance(artifact,dict) or artifact.get('kind')!=expected_kind(request)):
                        raise
                    # Preserve an identified but schema-invalid envelope as
                    # opaque text. Never infer/drop fields to admit its data.
                    reason=str(error)+'; '+str(schema_error)
                    write_private_json(str(prefix)+'.checks.json',{'transport_valid':False,'reason':reason,'raw_sha256':digest(raw.encode())})
                    raise RejectedEnvelope(raw,reason) from None
                if (response.get('call_id')!=call_id or response.get('stage')!=request['stage']
                        or not response.get('artifact') or response['artifact']['kind']!=expected_kind(request)):raise
                write_private_json(str(prefix)+'.checks.json',{'transport_valid':False,'reason':str(error)})
                raise RejectedResponse(response,str(error)) from None
            if constructor_normalization is not None:
                write_private_json(str(prefix)+'.constructor-normalization.json',constructor_normalization)
            try:
                checked=check_response(response,request,messages)
            except ProtocolError as error:
                # Only a bounded, identified, same-kind contract can become a
                # repair candidate. Malformed JSON/identity/kind is not promoted.
                try:schema_check(response,RESPONSE)
                except ProtocolError as schema_error:
                    if len(raw.encode())>2_000_000:raise
                    artifact=response.get('artifact')
                    if (response.get('call_id')!=call_id or response.get('stage')!=request['stage']
                            or not isinstance(artifact,dict) or artifact.get('kind')!=expected_kind(request)):
                        raise
                    write_private_json(str(prefix)+'.checks.json',{'transport_valid':False,'reason':str(schema_error),'raw_sha256':digest(raw.encode())})
                    raise RejectedEnvelope(raw,str(schema_error)) from None
                artifact=response['artifact']
                if (response['call_id']!=call_id or response['stage']!=request['stage'] or artifact is None
                        or artifact['kind']!=expected_kind(request)):
                    raise
                write_private_json(str(prefix)+'.checks.json',{'transport_valid':False,'reason':str(error)})
                raise RejectedResponse(response,str(error)) from None
            if expected_kind(request)=='dbc' and response['artifact'] is not None:self.dbc_candidate_seen=True
            write_private_json(str(prefix)+'.checks.json',checked)
            if (expected_kind(request) in {'fp','dbc','bp'} and response['artifact'] is not None
                    and checked['semantic_validation']['status']=='violated'):
                record['semantic_status']='violated'
                raise RejectedResponse(response,expected_kind(request)+' deterministic semantic checks violated',
                    category='contract_semantics',diagnostic=checked['semantic_validation'])
            record.update(status='response_checked',semantic_status=checked['semantic_validation']['status'])
            return response,checked
        except BaseException as error:
            record.update(status='error',error_type=type(error).__name__)
            raise
        finally:
            record['finished']=time.time();record['seconds']=record['finished']-started
            write_private_json(str(prefix)+'.state.json',record)

    def register(self,id,filename,kind,role,text,parents,*,os_role='shared',producer=None):
        if self.material_writer is not None:
            entry=self.material_writer(id,filename,kind,role,text,parents,os_role=os_role,producer=producer)
        else:
            path=self.evidence.root/'output/autodriver-call-derived'/filename
            if path.exists() or path.is_symlink():raise ProtocolError('derived call material cannot be overwritten')
            write_private_text(path,text)
            entry=dict(id=id,path=path.relative_to(self.evidence.root).as_posix(),kind=kind,approved=True,
                       sha256=digest(text.encode()),parents=list(parents),os_role=os_role)
            if producer:entry['producer']=producer
        self.evidence.registry[id]=entry

    def repair_request(self,current,rejected,number):
        kind=expected_kind(current)
        removed_roles={'candidate','internal_diagnostic','existing_fp','existing_dbc','existing_bp'}
        parents=list(dict.fromkeys(s['material_id'] for s in current['selections']))
        role='source' if kind=='fp' and any(self.evidence.registry[id]['os_role']=='source' for id in parents) else 'target' if kind in {'fp','bp','code'} else 'shared'
        candidate_id=current['call_id']+'.rejected'
        artifact=None if isinstance(rejected,RejectedEnvelope) else rejected.response['artifact']
        retained_base=None
        if kind!='code' and (artifact is None or artifact.get('format')=='constructor_edits'):
            # Rejected edits are not a replacement contract. Keep the trusted
            # visible base, rather than showing only an unusable patch as the
            # next candidate. Never trust the response's claimed base ID/hash.
            from .contract_patch import repair_base
            candidates=[]
            for selection in current['selections']:
                if selection['role']!='candidate':continue
                text=self.evidence.read(selection['material_id']).decode()
                total=max(1,len(text.splitlines()))
                candidates.append(dict(role='candidate',material_id=selection['material_id'],text=text,
                    sha256=self.evidence.registry[selection['material_id']]['sha256'],total_lines=total,
                    start_line=selection.get('start_line',1),end_line=selection.get('end_line',total)))
            retained_base=repair_base(current,kind,candidates)
        if retained_base is not None:candidate_id=retained_base['material_id']
        else:
            candidate_text=rejected.raw if artifact is None else canonical(artifact) if kind=='code' or not isinstance(artifact.get('content'),str) else artifact['content']
            self.register(candidate_id,candidate_id+('.json' if kind=='code' else '.constructor'),
                          'fp_task_local' if kind=='fp' else 'candidate_code' if kind=='code' else kind+'_candidate','candidate',
                          candidate_text+'\n',parents,os_role=role)
        diagnostic_id=current['call_id']+'.protocol-diagnostic'
        category=getattr(rejected,'category','response_protocol')
        diagnostic={'checker':'method_contract_checker','category':category,'reason':str(rejected),
                    'semantic_checks':getattr(rejected,'diagnostic',None),
                    'original_evidence_links':None if artifact is None else rejected.response['evidence_links'],
                    'original_unresolved':None if artifact is None else rejected.response['unresolved'],
                    'opaque_invalid_envelope':artifact is None,
                    'scope':'fix this single object using original supplied evidence; no behavioral validation claim'}
        if self.last_messages is not None:
            inventory=[];handles={}
            for item in json.loads(self.last_messages[-1]['content'])['context']:
                if item['role'] in removed_roles:continue
                key=(item['material_id'],item['sha256'],item['start_line'],item['end_line'])
                if key not in handles:handles[key]='c'+str(len(handles)+1)
                if item['role'] in {'method_specification','validation_report'} or not item.get('evidence_eligible',True):continue
                inventory.append({**{key:item[key] for key in ('material_id','kind','start_line','end_line')},'citation_id':handles[key]})
            diagnostic['source_citation_inventory']=(inventory if len(canonical(inventory).encode())<=32768 else
                {'location':'complete current context metadata','entries':len(inventory),'reason':'redundant diagnostic inventory exceeds its own bound; no source excerpts omitted'})
            diagnostic['citation_instruction']='Use only citation_id values from the current context for source support. original_evidence_links and rejected response citations are untrusted output to repair, not supplied evidence; do not repeat unprovided handles. Read-only DBC/FP/integration parents may support BP claims; diagnostics/candidates do not establish original facts.'
        if retained_base is not None:
            diagnostic.update(rejected_contract_edits=artifact,retained_base=retained_base,
                scope='edits were refused and never applied; the unchanged complete base remains the candidate. Repair the explicit operations or return a complete same-object candidate; all original evidence and semantic requirements remain in force')
            if artifact is None:diagnostic['rejected_response_text']=rejected.raw
        self.register(diagnostic_id,diagnostic_id+'.json','internal_diagnostic','internal_diagnostic',canonical(diagnostic)+'\n',
                      [candidate_id],os_role=role,producer='method_contract_checker')
        defect='确定性类型/单位/引用违规' if category=='contract_semantics' else '解析/协议错误'
        request=deepcopy(current);request.update(stage='repair',repair_target=kind,call_id=current['call_id']+'.r'+str(number),
            goal='仅修复 '+kind+' 对象的'+defect+'，并依据原始材料保留有据约束、证据与未决项；不修改其它父对象，不删除约束使检查表面通过。')
        # The old optional seed is already an ancestor of the new rejected
        # candidate. Its build-only role cannot be carried into repair.
        request['selections']=[s for s in request['selections'] if s['role'] not in removed_roles]
        request['selections'] += [{'role':'candidate','material_id':candidate_id},{'role':'internal_diagnostic','material_id':diagnostic_id}]
        return request

    def recover(self,request,*,saved_response=None):
        """Bounded question -> original evidence retrieval -> candidate loop.

        Refinement is a distinct later operation; this loop only answers explicit
        evidence requests. No model-proposed path is opened or executed.
        """
        current=deepcopy(request)
        history=[]
        attempt=0;repairs=0;protected_materials=set()
        while attempt<=self.maximum_refinements:
            try:
                response,checked=self.call(current,saved_response=saved_response,protected_materials=protected_materials)
            except (RejectedResponse,RejectedEnvelope) as error:
                if self.last_request and self.last_request['call_id']==current['call_id']:
                    current=deepcopy(self.last_request)
                history.append(current['call_id'])
                if repairs>=self.maximum_repairs:raise
                repairs+=1;current=self.repair_request(current,error,repairs)
                saved_response=None
                continue
            current=deepcopy(self.last_request)
            history.append(current['call_id'])
            saved_response=None
            if response['outcome']!='needs_evidence':return response,checked,history
            if attempt==self.maximum_refinements:break
            executor=ToolExecutor(self.evidence,current,configuration=self.configuration)
            added=[];batches=[]
            queries=source_query_batches(response['context_requests'],executor.specs())
            for query,members in queries:
                if query['scope']=='internal_tools':
                    if len(members)>1:
                        before=len(executor.events)
                        combined=self.tool_query(current,executor,query,'batch'+str(len(batches)))
                        returned=len(executor.events)>before and executor.events[-1]['status']=='returned'
                        batches.append({'member_indices':members,'query':query,'status':'returned' if returned else 'fallback_to_individual_queries'})
                        if returned:added+=combined
                        else:
                            # A missing root or union resource bound must not
                            # discard the other independent complete answers.
                            for member in members:added+=self.tool_query(current,executor,response['context_requests'][member],len(added))
                        continue
                    added+=self.tool_query(current,executor,query,len(added))
                    continue
                if query['scope'] not in {'source','device_documents','source_framework','target_framework','target_support'}:
                    continue
                try:
                    if query['scope'] in {'source_framework','target_framework'}:
                        result=executor.execute('query_framework',{'scope':query['scope'],'query':query['query']}) if 'query_framework' in executor.specs() else None
                    elif query['scope']=='device_documents':
                        result=executor.execute('retrieve_device_evidence',{'query':query['query']}) if 'retrieve_device_evidence' in executor.specs() else None
                    else:
                        result=self.evidence.search(executor.scope_ids(query['scope']),query['query'].split()[:16])
                except ValueError as error:
                    # A malformed literal lookup is bounded feedback, not an
                    # implicit tool command, a source fact, or a process crash.
                    id=current['call_id']+'.retrieval-refused.'+str(len(added))
                    self.register(id,id+'.json','internal_diagnostic','internal_diagnostic',canonical({
                        'status':'unknown','query':query,'reason':str(error),
                        'instruction':'Text scopes accept bounded literal search terms. Tool commands require scope=internal_tools and the supplied finite tool schema. No query was executed as a command; no source guarantee follows.'})+'\n',
                        list(dict.fromkeys(s['material_id'] for s in current['selections'])),producer='method_contract_checker')
                    added.append({'role':'internal_diagnostic','material_id':id})
                    continue
                if result:
                    role={'source':'source_evidence','device_documents':'document_evidence',
                          'source_framework':'framework_evidence','target_framework':'framework_evidence','target_support':'target_support'}[query['scope']]
                    for excerpt in result['excerpts']:
                        selected={k:excerpt[k] for k in ('material_id','start_line','end_line')};selected['role']=role
                        added.append(selected)
            write_private_json(self.output/(current['call_id']+'.retrieval.json'),{'tools':executor.events,'added_selections':added,
                'source_framework_projection':self.framework_replacements.get(current['call_id']),'source_query_batches':batches})
            if not added:
                # A failed lookup is useful bounded feedback, not a source
                # fact. Expose only registered approved material identities.
                id=current['call_id']+'.retrieval-miss'
                originals=[]
                for scope in ('source','device_documents','target_support'):
                    for material in executor.scope_ids(scope):
                        entry=self.evidence.registry[material]
                        originals.append({'scope':scope,'material_id':material,'path':entry['path'],'sha256':entry['sha256']})
                self.register(id,id+'.json','internal_diagnostic','internal_diagnostic',canonical({
                    'status':'unknown','queries':response['context_requests'],'matches':0,'approved_materials':originals,
                    'instruction':'No excerpt matched these queries. This is not evidence of any device/OS guarantee. Tool invocation requires scope=internal_tools and query JSON containing tool and arguments; other scopes perform text lookup. Use the actual supplied tools/material identities. A missing target device implementation is expected for a new driver; design internal code from the read-only contracts and public ABI, never request hidden/reference code.'})+'\n',
                    list(dict.fromkeys(s['material_id'] for s in current['selections'])),producer='method_contract_checker')
                added.append({'role':'internal_diagnostic','material_id':id})
            selected=current['selections']
            if current['call_id'] in self.framework_replacements:
                selected=[item for item in selected if not (item['role']=='framework_evidence'
                    and self.evidence.registry[item['material_id']]['os_role']=='source')]
            if any(item['role']=='source_facts' for item in added):
                # A concrete whole-function graph answers the review-index
                # question. Keep raw source/manuals and replace that redundant
                # index, rather than growing both representations indefinitely.
                selected=[item for item in selected if not (item['role']=='source_facts' and item['material_id']=='source.review-index')]
            protected_materials.update(item['material_id'] for item in added)
            merged=merge_selections(selected,added,self.evidence)
            def coordinates(items):
                return sorted((s['role'],s['material_id'],s.get('start_line',1),s.get('end_line',len(self.evidence.read(s['material_id']).decode('utf8').splitlines()))) for s in items)
            if coordinates(merged)==coordinates(current['selections']):
                id=current['call_id']+'.retrieval-stalled'
                self.register(id,id+'.json','internal_diagnostic','internal_diagnostic',canonical({
                    'status':'unknown','queries':response['context_requests'],'new_evidence':False,
                    'instruction':'These bounded queries returned only evidence already supplied. No additional provider or semantic guarantee was established. Use the supported facts to produce a partial candidate with explicit unresolved requirements where possible; otherwise report blocked. Do not merge incompatible providers, invent guarantees, or repeat this lookup as new evidence.'})+'\n',
                    list(dict.fromkeys(s['material_id'] for s in current['selections'])),producer='method_contract_checker')
                merged.append({'role':'internal_diagnostic','material_id':id})
            current['selections']=merged
            if expected_kind(current)=='code' and current['stage']!='repair':
                # Every generation continuation consumes the same four code
                # repairs, including evidence requests without any candidate.
                id=current['call_id']+'.unsubmitted-response'
                self.register(id,id+'.json','candidate_code','candidate',canonical(response)+'\n',
                    list(dict.fromkeys(s['material_id'] for s in current['selections'])),os_role='target')
                current['selections'].append({'role':'candidate','material_id':id})
                diagnostic_id=id+'.diagnostic'
                self.register(diagnostic_id,diagnostic_id+'.json','internal_diagnostic','internal_diagnostic',canonical({
                    'status':'unknown','reason':'Previous code generation requested evidence without an accepted candidate.',
                    'queries':response['context_requests'],
                    'scope':'Continue only this code object using the newly returned approved evidence/lookup diagnostics and the same baseline/parents. This continuation consumes the cumulative four code repairs; no prior code or source guarantee is inferred.'})+'\n',
                    [id],producer='method_contract_checker')
                current['selections'].append({'role':'internal_diagnostic','material_id':diagnostic_id})
                current.update(stage='repair',repair_target='code')
            attempt+=1
            current['call_id']=request['call_id']+'.e'+str(attempt)
        return response,{'transport_valid':True,'semantic_validation':{'status':'unknown','reason':'bounded evidence recovery exhausted'}},history

    def tool_query(self,current,executor,query,number):
        """Explicit finite tool invocation via the existing evidence envelope."""
        parents=list(dict.fromkeys(item['material_id'] for item in current['selections']))
        id=current['call_id']+'.tool-result.'+str(number)
        framework=[];carrier='json'
        try:
            command=json.loads(query['query'])
            if not isinstance(command,dict) or set(command)!={'tool','arguments'}:raise ValueError('tool query requires only tool and arguments')
            name=command['tool'];arguments=command['arguments']
            if not isinstance(name,str) or name not in executor.specs():raise ValueError('tool is not available in this stage/condition')
            result=executor.execute(name,arguments)
            if name in {'slice_device_effects','resolve_configuration'}:
                from .context_tables import render_tool_result
                kind,role,producer='source_analysis','source_facts',None
                parents=executor.scope_ids('source')+[key for key,entry in self.evidence.registry.items() if entry['kind']=='source_configuration']
                if name=='slice_device_effects':
                    # Select source API originals from ALL active input units,
                    # not just a partial indirect-call slice. The complete raw
                    # device/manual inputs and every analysis gap stay selected.
                    index=executor.index('source_framework')
                    external=result['translation_unit_external_calls']
                    roots=[symbol for symbol in external if symbol in index.by_name]
                    closure=index.excerpts(roots,maximum_lines=6000) if roots else {'selections':[],'gaps':[]}
                    result['source_api_retrieval']={'roots':roots,'gaps':closure['gaps'],
                        'unresolved_external_calls':[symbol for symbol in external if symbol not in index.by_name],
                        'scope':'complete indexed declaration/call/type closure for active translation-unit external calls; unresolved indirect/provider effects remain unknown'}
                    if closure['selections']:
                        framework=[dict(item,role='framework_evidence') for item in closure['selections']]
                        self.framework_replacements[current['call_id']]={**result['source_api_retrieval'],
                            'selections':framework,'scope':'replace the previous broad source-framework excerpts only; same approved original pool remains queryable'}
                        parents+=list(dict.fromkeys(item['material_id'] for item in framework))
                text,carrier=render_tool_result(result)
            elif name=='check_contract':
                kind,role,producer='internal_diagnostic','internal_diagnostic','method_contract_checker'
                text=canonical({'tool':name,'checks':result,'scope':'method deterministic contract checks only; no evidence entailment or candidate execution'})+'\n'
            else:
                scope=arguments.get('scope','device_documents');role='framework_evidence' if scope.endswith('_framework') else 'source_evidence' if scope=='source' else 'target_support' if scope=='target_support' else 'document_evidence'
                selected=[dict(role=role,**{key:excerpt[key] for key in ('material_id','start_line','end_line')}) for excerpt in result.get('excerpts',[])]
                if name=='inspect_symbol' and result['indexed_material_ids']:
                    index_kind='source_index' if scope=='source' else 'framework_index'
                    index_role='source_facts' if scope=='source' else 'framework_evidence'
                    metadata={'schema':'autodriver.symbol-query-index/v1','index_kind':index_kind,'facts_available':False,
                        'query_scope':scope,'requested_exact_selectors':result['roots'],'gaps':result['gaps'],
                        'parser_index_gaps':result['index_gaps'],'indexed_name_count':result['indexed_name_count'],
                        'exact_indexed_names':result['exact_indexed_names'],
                        'mechanical_index_closure_complete':result['complete'] and not result['index_gaps'],
                        'scope':result['index_scope'],
                        'catalog_limit':'Full indexed names supplied only when at most128; otherwise use literal source/framework lookup. An omitted catalog proves no absence.',
                        'instruction':'This is a non-factual locator and unresolved-query report. Only returned original excerpts can support source assertions. Use exact selectors from this approved scope; multiple selectors use an array or whitespace-separated names. A missing source symbol does not authorize reading a target/reference driver.'}
                    self.register(id,id+'.json',index_kind,index_role,canonical(metadata)+'\n',result['indexed_material_ids'],
                        os_role='source' if scope in {'source','source_framework'} else 'target')
                    selected.append({'role':index_role,'material_id':id})
                return selected
        except (ValueError,TypeError,KeyError) as error:
            kind,role,producer='internal_diagnostic','internal_diagnostic','method_contract_checker'
            text=canonical({'tool_request':query['query'],'status':'unknown','reason':str(error),
                'scope':'finite tool request refused; no command/path fallback and no semantic guarantee'})+'\n'
        if len(text.encode())>2_000_000:raise ProtocolError('complete tool result exceeds bounded material size; ask a smaller complete question')
        self.register(id,id+'.'+carrier,kind,role,text,parents,os_role='source' if kind=='source_analysis' else 'shared',producer=producer)
        return [{'role':role,'material_id':id}]+framework
