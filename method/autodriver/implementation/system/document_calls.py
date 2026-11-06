"""Real bounded call/tool loop with advisory contract responses.

The model backend is the existing local host gateway. Tool authority remains
the existing finite ToolExecutor; model output never supplies a filesystem path
or grants a scope. Raw responses, queries, refusals and exact messages persist.
"""
import json
import time

from .calls import decode_response, merge_selections
from .document_context import assemble_document
from .document_scope import compact_originals
from .semantic_documents import SemanticDocument
from .tools import ToolExecutor
from .variants import admit_call, variant as variant_policy
from .protocol.assembler import (ProtocolError, MethodLimitError, canonical, digest,
                                 expected_kind, write_private_json, write_private_text,
                                 readable_messages)


class DocumentCalls:
    def __init__(self, workspace, *, client=None, variant='full', maximum_calls=64,
                 maximum_refinements=3, maximum_tools=64):
        self.workspace=workspace
        self.client=client
        self.variant=variant
        self.policy=variant_policy(variant)
        if (not 1<=maximum_calls<=64 or not 0<=maximum_refinements<=3
                or not 0<=maximum_tools<=128):
            raise ValueError('finite document call/refinement/tool bounds required')
        self.maximum_calls=maximum_calls
        self.maximum_refinements=maximum_refinements
        self.maximum_tools=maximum_tools
        self.calls=[]
        self.tool_events=[]
        self.dbc_candidate_seen=False
        self.code_repairs=0
        self.last_request=None
        self.last_messages=None
        self.output=workspace.root/'output/requests'
        self.output.mkdir(parents=True,exist_ok=True)

    def interfaces(self, executor, request):
        if request.get('view_only'):
            return {}
        specs=executor.specs()
        if expected_kind(request)=='fp':
            scope=request['framework_role']+'_framework'
            for name in ('read_evidence_range','inspect_symbol','query_framework'):
                if name in specs and 'scope' in specs[name]['properties']:
                    specs[name]['properties']['scope']['enum']=[scope]
        return specs

    def query_requests(self, raw):
        try:
            value,_=decode_response(raw)
        except ProtocolError:
            return None,[],None
        if not isinstance(value,dict):
            return value,[],None
        queries=value.get('tool_requests',[])
        if not queries and value.get('context_requests'):
            queries=[]
            for query in value['context_requests']:
                if not isinstance(query,dict):
                    return value,[],'A context request must contain a finite JSON tool command.'
                try:
                    command=json.loads(query['query'])
                except (ValueError,TypeError,KeyError):
                    return value,[],'The requested finite tool command is not JSON.'
                if (not isinstance(command,dict) or set(command)!={'tool','arguments'}
                        or not isinstance(command['arguments'],dict)):
                    return value,[],'Use a JSON object containing only an advertised tool and its exact arguments.'
                # The response schema advertises original-evidence scopes as
                # well as internal_tools. Accept their machine commands with
                # matching scopes; executor schemas still enforce authority.
                if (query.get('scope')!='internal_tools'
                        and query.get('scope')!=command['arguments'].get('scope')):
                    return value,[],'Outer context scope must match the finite tool argument scope.'
                queries.append(command)
        if not isinstance(queries,list) or len(queries)>16:
            return value,[],'At most16 finite tool requests per response; no partial batch executed.'
        return value,queries,None

    def invoke(self, request, *, protected=()):
        if len(self.calls)>=self.maximum_calls:
            raise MethodLimitError('logical_calls',self.maximum_calls,len(self.calls))
        code_repair=request['stage']=='repair' and request.get('repair_target')=='code'
        if code_repair:
            if self.code_repairs>=4:
                raise MethodLimitError('code_repairs',4,self.code_repairs)
        request=dict(request,call_id='document-call-'+str(len(self.calls)+1).zfill(3)+'-'+request['stage'])
        selected,planning=compact_originals(self.workspace,request['selections'],request['call_id'],protected=protected)
        request['selections']=selected
        evidence=self.workspace.evidence()
        admit_call(self.variant,request,{'materials':list(evidence.registry.values())},
                   dbc_candidate_seen=self.dbc_candidate_seen)
        executor=ToolExecutor(evidence,request,configuration=self.workspace.macro_configuration())
        messages,audit=assemble_document(evidence,request,executor_specs=self.interfaces(executor,request))
        self.last_request,self.last_messages=request,messages
        prefix=self.output/request['call_id']
        write_private_json(str(prefix)+'.request.json',request)
        write_private_json(str(prefix)+'.messages.json',messages)
        write_private_json(str(prefix)+'.audit.json',dict(audit,scope_planning=planning))
        write_private_text(str(prefix)+'.model-input.md',readable_messages(messages))
        record=dict(call_id=request['call_id'],stage=request['stage'],repair_target=request.get('repair_target'),
                    artifact_role='derived_executable_view' if request.get('view_only') else 'primary_or_code',
                    started=time.time(),status='started',prompt_sha256=audit['prompt_sha256'])
        self.calls.append(record)
        if code_repair:
            self.code_repairs+=1
        try:
            client=self.client
            if client is None:
                from method.common.client import complete
                client=complete
            raw=client(messages[1]['content'],system=messages[0]['content'],stage=request['stage'],
                       step=request['call_id']+(':'+request['repair_target'] if 'repair_target' in request else ''))
            if not isinstance(raw,str):
                raise ProtocolError('model transport must return original text')
            write_private_text(str(prefix)+'.response.txt',raw)
            record['status']='response_captured'
            record['response_sha256']=digest(raw.encode())
            return raw,executor
        except BaseException as error:
            record.update(status='error',error_type=type(error).__name__)
            raise
        finally:
            record['finished']=time.time()
            record['seconds']=record['finished']-record['started']
            write_private_json(str(prefix)+'.state.json',record)

    def diagnostic(self, request, payload, number):
        material=request['call_id']+'.tool-diagnostic.'+str(number)
        parents=list(dict.fromkeys(item['material_id'] for item in request['selections']))
        self.workspace.add_derived(material,material+'.json','internal_diagnostic','internal_diagnostic',
                                   canonical(payload)+'\n',parents,
                                   os_role=request.get('framework_role','shared'),producer='method_contract_checker')
        return dict(role='internal_diagnostic',material_id=material)

    def execute_queries(self, request, queries, executor):
        additions=[]
        for number, query in enumerate(queries):
            if len(self.tool_events)>=self.maximum_tools:
                additions.append(self.diagnostic(request,dict(status='not_executed',reason='bounded tool count exhausted'),number))
                break
            event=dict(call_id=request['call_id'],query=query,status='refused')
            self.tool_events.append(event)
            try:
                if not isinstance(query,dict) or set(query)!={'tool','arguments'}:
                    raise ValueError('tool request needs only tool and finite arguments')
                name,args=query['tool'],query['arguments']
                if not isinstance(args,dict) or name not in self.interfaces(executor,request):
                    raise ValueError('unknown tool or malformed arguments')
                if expected_kind(request)=='fp' and args.get('scope',request['framework_role']+'_framework')!=request['framework_role']+'_framework':
                    raise ValueError('framework tool requested the other OS role')
                result=executor.execute(name,args)
                text=canonical(result)+'\n'
                result_path=self.workspace.generated/(request['call_id']+'.tool-result.'+str(number)+'.json')
                write_private_text(result_path,text)
                event.update(status='returned',result_sha256=digest(text.encode()))
                if len(text.encode())>131072:
                    additions.append(self.diagnostic(request,dict(status='not_supplied_to_model',
                        reason='Complete tool result exceeds reply bound; request narrower complete source/function/page scope.',
                        result_sha256=digest(text.encode()),query=query),number))
                    continue
                if result.get('excerpts'):
                    roles={'device_source':'source_evidence','document_extract':'document_evidence',
                           'framework_source':'framework_evidence','framework_document':'framework_evidence',
                           'public_target_support':'target_support'}
                    for excerpt in result['excerpts']:
                        entry=executor.evidence.registry[excerpt['material_id']]
                        if entry['kind'] not in roles:
                            raise ValueError('tool returned a non-original excerpt kind')
                        additions.append(dict(role=roles[entry['kind']],material_id=excerpt['material_id'],
                                              start_line=excerpt['start_line'],end_line=excerpt['end_line']))
                elif name in {'slice_device_effects','resolve_configuration','inspect_symbol','query_framework','retrieve_device_evidence','read_evidence_range'}:
                    material=request['call_id']+'.analysis.'+str(number)
                    parents=([id for id,entry in self.workspace.entries.items()
                              if entry['kind'] in {'device_source','source_configuration'}]
                             if name in {'slice_device_effects','resolve_configuration'} else
                             list(dict.fromkeys(item['material_id'] for item in request['selections'])))
                    self.workspace.add_derived(material,material+'.json','source_analysis' if name in {'slice_device_effects','resolve_configuration'} else 'internal_diagnostic',
                        'source_facts' if name in {'slice_device_effects','resolve_configuration'} else 'internal_diagnostic',
                        text,parents,os_role='source' if name in {'slice_device_effects','resolve_configuration'} else request.get('framework_role','shared'),
                        producer=None if name in {'slice_device_effects','resolve_configuration'} else 'method_contract_checker')
                    additions.append(dict(role='source_facts' if name in {'slice_device_effects','resolve_configuration'} else 'internal_diagnostic',material_id=material))
                else:
                    additions.append(self.diagnostic(request,result,number))
            except ProtocolError:
                # Hash/path/lineage drift is not a recoverable model query.
                raise
            except (ValueError,TypeError,KeyError) as error:
                event['status']='refused'
                event['reason']=str(error)
                additions.append(self.diagnostic(request,dict(status='refused',reason=str(error),query=query,
                    facts_established=False),number))
        write_private_json(self.workspace.generated/'document-tool-events.json',self.tool_events)
        return additions

    def run(self, request, *, parents=()):
        current=dict(request)
        protected=set()
        value=None
        for attempt in range(self.maximum_refinements+1):
            raw,executor=self.invoke(current,protected=protected)
            current=dict(self.last_request)
            value,queries,refusal=self.query_requests(raw)
            response_role=self.response_role(raw,value)
            if expected_kind(current)=='dbc' and response_role=='draft' and raw.strip():
                self.dbc_candidate_seen=True
            if refusal:
                current['selections']+= [self.diagnostic(current,dict(status='refused',reason=refusal),0)]
            preserve_first=(expected_kind(current)=='dbc' and self.dbc_candidate_seen
                            and not self.policy.dbc_evidence_refinement)
            stop_reason=('first_dbc_draft_preserved' if preserve_first else
                         'refinement_bound' if attempt==self.maximum_refinements and queries else
                         'continuing_evidence_recovery' if queries else 'no_tool_requests')
            write_private_json(self.output/(current['call_id']+'.checks.json'),dict(
                artifact_role='derived_executable_view' if current.get('view_only') else 'primary_or_code',
                response_role=response_role,contract_checks='advisory',
                semantic_validation=dict(status='unknown',reason='document not asserted to be verified'),
                tool_request_refusal=refusal,stop_reason=stop_reason,
                unexecuted_tool_requests=queries if preserve_first or attempt==self.maximum_refinements else []))
            if current.get('view_only') or not queries or attempt==self.maximum_refinements or preserve_first:
                break
            additions=self.execute_queries(current,queries,executor)
            protected.update(item['material_id'] for item in additions)
            current['selections']=merge_selections(current['selections'],additions,self.workspace.evidence())
            if expected_kind(current)=='code':
                current.update(stage='repair',repair_target='code')
        kind=expected_kind(current)
        if kind=='code' or current.get('view_only'):
            return raw
        role=self.response_role(raw,value)
        document=SemanticDocument(kind,self.workspace.task['task_id'],self.last_request['call_id'],
                                  self.last_request['stage'],raw,parents,
                                  current.get('framework_role','target' if kind=='bp' else 'shared'),role)
        if kind=='dbc' and document.availability=='present':
            self.dbc_candidate_seen=True
        return document

    @staticmethod
    def response_role(raw,value):
        if isinstance(value,dict):
            if value.get('outcome')=='blocked':
                return 'declared_blocked'
            if (value.get('tool_requests') or value.get('context_requests')) and not value.get('document') and not value.get('artifact'):
                return 'evidence_only'
        return 'draft'
