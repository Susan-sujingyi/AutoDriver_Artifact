"""Semantic-document FP/DBC/BP pipeline with advisory intermediate checks.

Exact prepared inputs and shared FP imports use the existing provenance code.
No rejected task contract or scoring feedback is imported. Host candidate-tool
integration uses a separate explicit advisory policy; no legacy open gate is
claimed here.
"""
import json
from pathlib import Path

from .pipeline import Pipeline as LegacyPipeline
from .document_calls import DocumentCalls
from .document_code import extract_code
from .calls import merge_selections
from .semantic_documents import SEMANTIC_GUIDE, ParentIdentity, SemanticDocument, advisory_generation_record
from .source_analysis import CAnalysis
from .evidence import SourceIndex
from .protocol.assembler import (canonical,digest,write_private_json,ProtocolError,
                                 MethodLimitError,ContextLimitError,path_allowed)


class DocumentPipeline(LegacyPipeline):
    def __init__(self,workspace,*,client=None,variant_name='full',maximum_calls=64,max_context_bytes=524288):
        super().__init__(workspace,client=client,variant_name=variant_name,
                         maximum_calls=maximum_calls,max_context_bytes=max_context_bytes,
                         semantic_guide=SEMANTIC_GUIDE)
        if self.contract_bundle or self.code_seed or self.runner.saved_responses:
            raise ValueError('document pipeline does not import legacy task contracts or code checkpoints')
        self.runner=DocumentCalls(self.workspace,client=client,variant=variant_name,maximum_calls=maximum_calls)
        self.documents={}
        self.document_imports=[]
        self.source_fact_selections=[]
        self.last_applied_proposal=None
        self.document_stage_attempts=0
        self.derived_views={}
        self.view_primary={}

    def request(self,stage,selections,goal,**extra):
        kind=extra.get('repair_target') if stage=='repair' else {'fp_build':'fp','dbc_recover':'dbc','bp_build':'bp','code_generate':'code'}[stage]
        return dict(schema='autodriver.document-call/v1',stage=stage,condition='FRAMEWORK' if kind=='fp' else 'SD',
                    goal=goal,selections=selections,max_context_bytes=self.max_context_bytes,
                    generation_policy='advisory_contract_checks',**extra)

    def parents_for(self,key):
        keys=(('source_fp',) if self.policy.source_framework_lifting else ()) if key=='dbc' else (
              ('dbc','target_fp') if key=='bp' else
              ('dbc','target_fp')+(('bp',) if self.policy.binding_plan else ()) if key=='code' else ())
        return {name:self.revisions.current[name] for name in keys}

    def capture(self,key,document,selections):
        parents=self.parents_for(key)
        artifact=dict(kind=document.kind,format='semantic_document',content=document.raw_text)
        revision=self.revisions.add(key,artifact,parents,provenance=dict(
            call_id=document.call_id,raw_sha256=document.raw_sha256,
            document_identity=document.identity,generation_policy='advisory_contract_checks'))
        self.discard_invalidated()
        material=key+'.document.'+revision.identity[:16]
        kind='fp_task_local' if key.endswith('_fp') else key+'_candidate'
        role={'source_fp':'source_fp','target_fp':'target_fp','dbc':'dbc','bp':'bp'}[key]
        origins=list(dict.fromkeys(s['material_id'] for s in selections))
        self.workspace.add_derived(material,material+'.json',kind,role,
            canonical(document.record())+'\n',origins,os_role=document.os_role)
        self.artifacts[key]=artifact
        self.material_ids[key]=material
        self.documents[key]=document
        self.checks[key]=dict(schema='autodriver.document-observation/v1',status='unknown',
                              availability=document.availability,executable_projection='not_checked',
                              assurance='not_asserted',findings=[])
        self.export_state()

    def run_document(self,key,stage,selections,goal,**extra):
        self.document_stage_attempts+=1
        parents=tuple(ParentIdentity(name,self.material_ids[name],self.workspace.entries[self.material_ids[name]]['sha256'])
                      for name in self.parents_for(key))
        try:
            document=self.runner.run(self.request(stage,selections,goal,**extra),parents=parents)
            self.capture(key,document,self.runner.last_request['selections'])
        except ContextLimitError as error:
            # A local contract-stage message cannot be sent at this bound.
            # Record no produced document, then let smaller downstream calls
            # use the same original pool. Do not raise the context limit.
            kind='fp' if key.endswith('_fp') else key
            document=SemanticDocument(kind,self.workspace.task['task_id'],
                'unissued-'+key+'-'+str(self.document_stage_attempts),'repair' if stage=='repair' else stage,
                '',parents,extra.get('framework_role','target' if kind=='bp' else 'shared'),
                'declared_blocked')
            self.capture(key,document,selections)
            self.checks[key]['local_context_failure']=dict(required_bytes=error.required_bytes,
                                                           limit_bytes=error.limit_bytes,
                                                           model_request_issued=False)
            self.export_state()
        return document

    def discard_invalidated(self):
        super().discard_invalidated()
        if hasattr(self,'documents'):
            self.documents={k:v for k,v in self.documents.items() if k in self.revisions.current}

    def fp(self,role):
        key=role+'_fp'
        saved=self.saved_objects.get(key)
        if saved and not saved.get('seed_only') and self.fp_scope_current(saved['root'],role):
            artifact=saved['artifact']
            revision=self.revisions.add(key,artifact,{},provenance=dict(
                producer='frozen_framework_import',origin=saved['origin'],
                framework_version=saved.get('framework_version'),new_upstream_requests=0))
            self.artifacts[key]=artifact
            self.material_ids[key]=saved['material_id']
            self.workspace.roles[saved['material_id']]=key
            self.checks[key]=saved['checks']
            self.documents[key]=SemanticDocument('fp',self.workspace.task['task_id'],
                'frozen-import-'+key,'fp_build',
                self.workspace.evidence().read(saved['material_id']).decode('utf8'),(),role)
            self.document_imports.append(dict(object=key,revision=revision.identity,
                framework_version=saved.get('framework_version'),origin=saved['origin'],new_upstream_requests=0))
            self.export_state()
            return True
        selected=[dict(role='method_specification',material_id='method'),
                  dict(role='framework_context',material_id=self.framework_scope[role])]
        selected+=self.framework_selections[role]
        if role=='target':
            from .native_abi import native_abi
            abi,audit=native_abi(self.workspace,self.framework_ids[role],self.framework_scope[role])
            selected=merge_selections(selected,abi,self.workspace.evidence())
            self.workspace.add_derived('target.fp.native-abi','target-fp-native-abi.json',
                'internal_diagnostic','validation_report',canonical(audit)+'\n',
                [self.framework_scope[role],*audit['source_materials']],producer='method_contract_checker')
        if saved:
            selected.append(dict(role='existing_fp',material_id=saved['material_id']))
        self.run_document(key,'fp_build',selected,
            'Recover relevant API/provider framework semantics for the supplied OS/configuration scope. '
            'Recover valid parameter/field domains and the actual entry/actor/effect that establishes each precondition, '
            'including success conditions, lifetime, invalidation and setup rollback from approved originals. '
            'An existing FP with a different retrieval scope is a readonly draft to refine, not a current guarantee. '
            'No device commands, policy, frames or formulas. Preserve actual signatures, effects and gaps. '
            'Use readable text/tables; do not require universal provider guarantees.',framework_role=role)
        return True

    def source_facts(self):
        index=SourceIndex(self.workspace.evidence())
        dependencies=CAnalysis(self.workspace.evidence(),self.workspace.macro_configuration())
        membership=self.workspace.source_membership()
        for material,entry in self.workspace.entries.items():
            if entry['kind']!='device_source':
                continue
            suffix=Path(entry['path']).suffix
            if suffix in {'.c','.h','.rs'}:
                index.add(material)
            if suffix in {'.c','.h'}:
                records=[r for r in membership if r['path']==entry['path']]
                if records and all(r['active'] is False for r in records):
                    dependencies.excluded.append(dict(material_id=material,sha256=entry['sha256'],
                        build_membership=records,reason='excluded by frozen configuration'))
                else:
                    dependencies.add(material)
        facts=dict(scope='mechanical syntax/control/data analysis, not device semantic proof',
                   symbols=[vars(s) for s in index.symbols.values()],gaps=index.gaps,
                   program_dependencies=dependencies.report(),build_membership=membership)
        parents=[id for id,e in self.workspace.entries.items() if e['kind'] in {'device_source','source_configuration'}]
        self.workspace.add_derived('source.syntax','source-syntax.json','source_analysis','source_facts',
                                   canonical(facts)+'\n',parents,os_role='source')
        self.source_fact_selections=[dict(role='source_facts',material_id='source.syntax')]
        framework=SourceIndex(self.workspace.evidence())
        for material in self.framework_ids['source']:
            if self.workspace.entries[material]['kind']=='framework_source':
                framework.add(material)
        external={c['callee'] for c in facts['program_dependencies']['calls'] if not c['targets']}
        roots=sorted(external & set(framework.by_name))
        if roots:
            self.source_fact_selections += [dict(s,role='framework_evidence')
                for s in framework.excerpts(roots,maximum_lines=6000)['selections']]

    def dbc(self):
        self.source_facts()
        selected=self.workspace.selections({'method_specification','task_context','source_configuration','source_evidence','document_evidence'})
        selected+=self.source_fact_selections
        if self.policy.source_framework_lifting:
            selected.append(dict(role='source_fp',material_id=self.material_ids['source_fp']))
        self.run_document('dbc','dbc_recover',selected,
            'Recover one global device behavior model from actual frozen source/configuration and original device documents. '
            'Follow selected policy, I/O/control/data dependencies and source API effects. State initialization, acquisition, '
            'failures/reentry, physical timing, calibration/sample lifetime and normative numeric relations. '
            'Keep conflicts and unknowns explicit. A readable draft can proceed to implementation.')
        if self.policy.dbc_evidence_refinement:
            candidate=self.material_ids['dbc']
            review='dbc.document-review'
            self.workspace.add_derived(review,review+'.json','internal_diagnostic','internal_diagnostic',canonical(dict(
                status='not_checked',purpose='Dedicated source/document evidence review, not a passed check',
                questions=['Cross-check the complete source control/data graph against device originals and the selected task policy.',
                           'Resolve actual source/device conflicts; preserve unresolved ones and their effect.',
                           'Separate calibration epochs, current sample identity and cached visibility.',
                           'Clarify one normative formula, input domain, output range, source approximation and target obligations.',
                           'Preserve failure terminal outcomes and conditional environmental progress assumptions.']))+'\n',
                [candidate],producer='method_contract_checker')
            self.run_document('dbc','repair',selected+[dict(role='candidate',material_id=candidate),
                dict(role='internal_diagnostic',material_id=review)],
                'Review and refine this DBC against approved original evidence. This is one-object repair; do not change source policy or framework guarantees.',
                repair_target='dbc',dbc_review=True)
        return True

    def bp(self):
        selected=self.workspace.selections({'method_specification','task_context','target_support'})
        selected += [dict(role=name,material_id=self.material_ids[name]) for name in ('dbc','target_fp')]
        selected += self.framework_selections['target']
        self.run_document('bp','bp_build',selected,
            'Map actual readonly device contract and target framework to the public native integration. '
            'For each FP precondition bind its responsible actor, triggering entry/guard, concrete establishing '
            'call or field initialization on the exact instance, valid inputs, success event, next state and failure/cleanup. '
            'Describe operation/event/data mappings, request states, callback/resource ownership, physical waits '
            'and numeric output encoding. Approved target framework excerpts support recovering missing native details; '
            'a conflict or missing FP guarantee must stay explicit for directed FP repair, not be overwritten in BP. '
            'Preserve gaps; use readable mappings, not fragile AST paths.')
        return True

    def baseline_context(self):
        editable=['target/'+p for p in self.workspace.task['submission']['editable_paths']]
        selected=[]
        bases={}
        request=dict(editable_paths=editable)
        for path,entry in self.workspace.manifest['files'].items():
            if path.startswith('target/') and path_allowed(path,request):
                if len(bases)>=128:
                    raise ValueError('candidate baseline file count exceeded')
                material='document.code-baseline.'+str(len(bases))
                self.workspace.add_original(material,path,'public_target_support','target_code',os_role='target')
                bases[path]=entry['sha256']
                selected.append(dict(role='target_code',material_id=material))
        return editable,bases,selected

    def code_context(self):
        selected=self.workspace.selections({'method_specification','task_context','target_support'})
        from .native_abi import native_abi
        abi,audit=native_abi(self.workspace,self.framework_ids['target'],self.framework_scope['target'])
        selected+=abi
        abi_material='code.native-abi.'+str(len(self.workspace.entries))
        self.workspace.add_derived(abi_material,abi_material+'.json','internal_diagnostic','validation_report',
            canonical(audit)+'\n',[self.framework_scope['target'],*audit['source_materials']],producer='method_contract_checker')
        selected.append(dict(role='validation_report',material_id=abi_material))
        selected += [dict(role=key,material_id=self.material_ids[key]) for key in ('dbc','target_fp')]
        if self.policy.binding_plan:
            selected.append(dict(role='bp',material_id=self.material_ids['bp']))
        policy=advisory_generation_record(list(self.documents.values()),[self.checks[k] for k in self.documents],
                                          expected_contracts=('dbc','target_fp','bp') if self.policy.binding_plan else ('dbc','target_fp'))
        self.checks['generation_policy']=policy
        material='document.generation-policy.'+str(len(self.workspace.entries))
        self.workspace.add_derived(material,material+'.json','internal_diagnostic','validation_report',
            canonical(policy)+'\n',[self.material_ids[k] for k in self.parents_for('code')],producer='method_contract_checker')
        selected.append(dict(role='validation_report',material_id=material))
        return selected

    def propose_document_code(self):
        editable,bases,baseline=self.baseline_context()
        selected=self.code_context()+baseline
        request=self.request('code_generate',selected,
            'Generate the native target driver from the actual semantic drafts and approved originals. '
            'Use the preferred complete source fence when supplied, or cumulative file_edits against the original baseline. Preserve public ABI and selected policy; '
            'unknown guarantees are not facts. Missing editable files must be created. The public integration '
            'describes the future driver you must implement; its private fields, state and method bodies are '
            'implementation choices, not missing existing-source prerequisites. Use the supplied original ABI '
            'declarations for consumed framework types and callbacks, and finite tools for additional originals. '
            'Contract format or unavailable proofs do not prevent this attempt.',
            editable_paths=editable,base_files=bases,
            generation_mode='with_binding' if self.policy.binding_plan else 'without_binding',
            generation_scope='best-effort implementation with explicitly advisory contract checks')
        result=self._code_attempt(request)
        if self.code_proposal is not None:
            from .document_view_compiler import compile_views
            compile_views(self)
        return result

    def _code_attempt(self,request):
        while True:
            raw=self.runner.run(request)
            current=self.runner.last_request
            try:
                artifact,report=extract_code(raw,current)
                break
            except ProtocolError as error:
                if self.runner.code_repairs>=4:
                    return self.result('code_response_repair_exhausted','code_generate',failure=str(error))
                material='code.response-diagnostic.'+str(len(self.runner.calls))
                self.workspace.add_derived(material,material+'.json','internal_diagnostic','internal_diagnostic',canonical(dict(
                    schema='autodriver.edit-carrier-diagnostic/v1',
                    status='refused',reason=str(error),raw_response_sha256=digest(raw.encode()),
                    rejected_response_text=raw,
                    scope='edit carrier/path/baseline only; no contract guarantee established'))+'\n',
                    [s['material_id'] for s in current['selections']],producer='method_edit_checker')
                request=dict(current,stage='repair',repair_target='code',
                    selections=current['selections']+[dict(role='internal_diagnostic',material_id=material)])
        parents=self.parents_for('code')
        revision=self.revisions.add('code',artifact,parents,provenance=dict(call_id=current['call_id'],
            raw_response_sha256=digest(raw.encode()),generation_policy='advisory_contract_checks'))
        material='code.'+revision.identity[:16]
        self.workspace.add_derived(material,material+'.json','candidate_code','candidate',canonical(artifact)+'\n',
            list(dict.fromkeys([self.material_ids[k] for k in parents]+[s['material_id'] for s in current['selections']
                if s['role'] in {'framework_evidence','target_support','target_code'}])),os_role='target')
        self.artifacts['code']=artifact
        self.material_ids['code']=material
        self.checks['code']=dict(status='unknown',edit_scope=report,implementation_conformance='not_checked')
        self.code_proposal=dict(artifact=artifact,request=current,revision=revision.identity,
            applied=False,full_scope_gate='not_required_advisory',generation_policy='advisory_contract_checks',
            generation_scope='best-effort native implementation; contracts and proof limitations remain visible')
        write_private_json(self.output/'code-proposal.json',self.code_proposal)
        self.export_state()
        return self.result('candidate_proposed','code_generate')

    def repair_code(self,diagnostics,*,prior_proposal=None):
        allowed={'approved_compile_tool'}|({'method_trace_checker'} if self.policy.candidate_contract_repair else set())
        if not diagnostics or any(d.get('producer') not in allowed for d in diagnostics):
            raise ValueError('unapproved candidate repair diagnostic')
        if self.runner.code_repairs>=4:
            raise MethodLimitError('code_repairs',4,self.runner.code_repairs)
        proposal=self.code_proposal if prior_proposal is None else prior_proposal
        if proposal is None:
            raise ValueError('code repair needs an actual proposed candidate')
        request=dict(proposal['request'],stage='repair',repair_target='code')
        selected=self.code_context()+[s for s in request['selections'] if s['role']=='target_code']
        selected.append(dict(role='candidate',material_id='code.'+proposal['revision'][:16]))
        for number,diagnostic in enumerate(diagnostics):
            material='code.tool-diagnostic.'+str(len(self.runner.calls))+'.'+str(number)
            self.workspace.add_derived(material,material+'.json','internal_diagnostic','internal_diagnostic',
                canonical(diagnostic)+'\n',['code.'+proposal['revision'][:16]],
                producer=diagnostic['producer'])
            selected.append(dict(role='internal_diagnostic',material_id=material))
        request['selections']=selected
        request['goal']='Repair only this code using supplied public/compiler or method-owned diagnostics. Return cumulative complete edits against the original baseline; contract facts and public ABI are readonly.'
        result=self._code_attempt(request)
        if self.code_proposal is not None:
            from .document_view_compiler import compile_views
            compile_views(self)
        return result

    def repair_candidate_prerequisite(self,diagnostics):
        """Repair one identified upstream object, then rebuild its dependents.

        Diagnostic findings identify gaps; they are never original evidence.
        A disabled refinement remains disabled even after native execution.
        """
        if not self.policy.candidate_contract_repair:
            raise ValueError('candidate contract feedback is disabled')
        if not diagnostics or any(d.get('producer')!='method_trace_checker' for d in diagnostics):
            raise ValueError('unapproved upstream diagnostic')
        findings=[f for d in diagnostics for f in d.get('findings',[])]
        if not findings or any(f.get('object') not in {'dbc','target_fp','bp'} for f in findings):
            raise ValueError('finite upstream object findings required')
        key=min((f['object'] for f in findings),key=('dbc','target_fp','bp').index)
        if key=='dbc' and not self.policy.dbc_evidence_refinement or key=='bp' and not self.policy.binding_plan:
            return False
        if key=='target_fp':
            selected=[dict(role='method_specification',material_id='method'),
                      dict(role='framework_context',material_id=self.framework_scope['target'])]+self.framework_selections['target']
            # Candidate-dependent diagnostics are not framework evidence and
            # cannot bring device/code ancestry into a framework-only call.
            goal='Re-examine the approved target API/provider evidence. Recover exact valid argument/field domains and actual establishing actions/actors, their success-conditioned postconditions and invalidation. Make genuine precondition, completion, ownership and numeric-output gaps explicit. Do not invent framework guarantees.'
            extra=dict(repair_target='fp',framework_role='target')
        else:
            roles={'method_specification','task_context','target_support'} if key=='bp' else {
                'method_specification','task_context','source_configuration','source_evidence','document_evidence'}
            selected=self.workspace.selections(roles)
            if key=='dbc':
                selected+=self.source_fact_selections
                if self.policy.source_framework_lifting:
                    selected.append(dict(role='source_fp',material_id=self.material_ids['source_fp']))
            else:
                selected += [dict(role=name,material_id=self.material_ids[name]) for name in ('dbc','target_fp')]
                selected += self.framework_selections['target']
            diagnostic='upstream.document-diagnostic.'+str(len(self.runner.calls))
            self.workspace.add_derived(diagnostic,diagnostic+'.json','internal_diagnostic','internal_diagnostic',
                canonical(dict(producer='method_trace_checker',findings=[f for f in findings if f['object']==key],
                               status='unknown',scope='missing checker prerequisites, not source facts'))+'\n',
                [self.material_ids[key]],producer='method_trace_checker')
            selected.append(dict(role='internal_diagnostic',material_id=diagnostic))
            goal='Repair only the named upstream semantic document against actual approved originals and these prerequisite findings. Preserve unresolvable gaps. Do not alter device policy or another contract.'
            extra=dict(repair_target=key)
        selected.append(dict(role='candidate',material_id=self.material_ids[key]))
        self.run_document(key,'repair',selected,goal,**extra)
        if self.policy.binding_plan and 'bp' not in self.revisions.current:
            self.bp()
        return True

    def export_state(self):
        materials=[dict(entry) for entry in self.workspace.entries.values()]
        objects={key:dict(material_id=self.material_ids[key],revision=self.revisions.current[key],
                          artifact_format=self.artifacts[key]['format']) for key in self.artifacts}
        write_private_json(self.output/'contract-state.json',dict(schema='autodriver.document-contract-state/v1',
            task_id=self.workspace.task['task_id'],input_identity=self.workspace.manifest['identity'],
            variant=self.variant_name,generation_policy='advisory_contract_checks',objects=objects,
            materials=materials,documents={k:v.record() for k,v in self.documents.items()},
            checks=self.checks,imports=self.document_imports,
            derived_views=getattr(self,'derived_views',{}),
            native_projection_status='not_checked',scope='exact prepared material paths/hashes; no independent score or hardware'))

    def result(self,status,stage,**extra):
        return dict(status=status,stage=stage,logical_calls=len(self.runner.calls),
            code_repairs_used=self.runner.code_repairs,variant=self.variant_name,
            generation_policy='advisory_contract_checks',candidate=None,checks=self.checks,
            revision_ids=dict(self.revisions.current),hardware_status='not_run',**extra)

    def code_repairs_used(self):
        return self.runner.code_repairs

    def run(self,*,stop_after=None):
        try:
            self.select()
            if self.policy.source_framework_lifting:
                self.fp('source')
            self.dbc()
            self.fp('target')
            if self.policy.binding_plan:
                self.bp()
            if stop_after=='bp':
                return self.result('development_stopped_after_contracts','bp')
            return self.propose_document_code()
        except MethodLimitError as error:
            result=self.result('context_limit_exhausted' if isinstance(error,ContextLimitError) else 'method_limit_exhausted',
                'bounded_recovery',failure=dict(type=type(error).__name__,reason=str(error),bound=error.kind,limit=error.limit,used=error.used))
            write_private_json(self.output/'pipeline-result.json',result)
            return result
