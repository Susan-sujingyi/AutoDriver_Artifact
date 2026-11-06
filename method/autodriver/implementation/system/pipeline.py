"""SD contract recovery pipeline with explicit upstream gates.

This development implementation cannot open generation merely because transport
validation passed. Unresolved obligations are retained as an upstream stop.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .workspace import Workspace
from .framework import select_framework
from .calls import CallRunner,RejectedResponse,RejectedEnvelope
from .revisions import Revisions
from .variants import variant
from .protocol.assembler import write_private_json,canonical,digest,ContextLimitError,MethodLimitError
from .contract.model import parse,identity,walk,ContractError
from .contract.data import check_layout
from .contract.binding import check_binding,binding_inventory
from .contract.check import check_contract,include_response_gaps
from .contract.admission import generation_admission
from .contract.support import audit_support
from .evidence import SourceIndex
from .protocol.assembler import check_response,read_pinned
from .source_analysis import CAnalysis
from .artifacts import import_bundle,references_for,semantic_identity,rebase_constructor


class Pipeline:
    def __init__(self,workspace,*,client=None,variant_name='full',maximum_calls=64,max_context_bytes=524288,semantic_guide=None):
        self.workspace=Workspace(workspace).bootstrap(semantic_guide=semantic_guide)
        self.output=self.workspace.generated
        self.policy=variant(variant_name)
        self.variant_name=variant_name
        self.runner=CallRunner(self.workspace.evidence(),Path(workspace)/'output/requests',client=client,
                               maximum_calls=maximum_calls,variant=variant_name,configuration=self.workspace.macro_configuration())
        self.runner.material_writer=self.write_call_material
        self.revisions=Revisions(self.output/'revisions')
        self.max_context_bytes=max_context_bytes
        self.version=0
        self.code_seed=None
        if os.environ.get('AUTODRIVER_CODE_CHECKPOINT'):
            path=Path(os.environ['AUTODRIVER_CODE_CHECKPOINT']).resolve(strict=True)
            if path.parent!=self.workspace.root/'output' or path.is_symlink():raise ValueError('code checkpoint must be host prepared')
            data=read_pinned(self.workspace.root,dict(id='host-code-checkpoint',path=path.relative_to(self.workspace.root).as_posix(),
                sha256=os.environ['AUTODRIVER_CODE_CHECKPOINT_SHA256']))
            self.code_seed=json.loads(data)
            if (variant_name!='full' or self.code_seed.get('schema')!='autodriver.code-checkpoint/v1'
                    or self.code_seed['task_id']!=self.workspace.task['task_id'] or self.code_seed['input_identity']!=self.workspace.manifest['identity']
                    or not 1<=self.code_seed['repair_depth']<=4):raise ValueError('code checkpoint scope/depth mismatch')
        self.artifacts={};self.material_ids={};self.checks={};self.evidence_reviews={}
        self.framework_ids={};self.framework_scope={}
        self.framework_selections={}
        self.fp_checkpoints={}
        self.fp_seeds={}
        if os.environ.get('AUTODRIVER_REJECTED_FP_SEEDS'):
            path=Path(os.environ['AUTODRIVER_REJECTED_FP_SEEDS'])
            if path.parent!=self.workspace.root/'output' or path.is_symlink():raise ValueError('rejected FP seeds must be host prepared')
            data=read_pinned(self.workspace.root,dict(id='host-rejected-fp-seeds',path=path.relative_to(self.workspace.root).as_posix(),
                sha256=os.environ['AUTODRIVER_REJECTED_FP_SEEDS_SHA256']))
            self.fp_seeds=json.loads(data)
            if set(self.fp_seeds)-{'source','target'}:raise ValueError('unknown rejected FP role')
        self.context_checkpoints={}
        self.contract_bundle=None;self.saved_objects={};self.import_audit=None;self.responses={}
        self.framework_packs={}
        if os.environ.get('AUTODRIVER_FRAMEWORK_PACKS'):
            path=Path(os.environ['AUTODRIVER_FRAMEWORK_PACKS'])
            if path.parent!=self.workspace.root/'output' or path.is_symlink():raise ValueError('framework packs must be host prepared')
            data=read_pinned(self.workspace.root,dict(id='host-framework-packs',path=path.relative_to(self.workspace.root).as_posix(),sha256=os.environ['AUTODRIVER_FRAMEWORK_PACKS_SHA256']))
            self.framework_packs=json.loads(data)
            if set(self.framework_packs)-{'source','target'}:raise ValueError('unknown framework consumer role')
            if 'source' in self.framework_packs and not self.policy.source_framework_lifting:raise ValueError('source FP is disabled in this intervention')
        self.code_proposal=None
        self.upstream_repair_attempts=[]
        artifact_path=os.environ.get('AUTODRIVER_CONTRACT_BUNDLE')
        if artifact_path:
            path=Path(artifact_path)
            if path.parent!=self.workspace.root/'output' or path.is_symlink():raise ValueError('contract bundle must be host prepared')
            data=read_pinned(self.workspace.root,dict(id='host-contract-bundle',path=path.relative_to(self.workspace.root).as_posix(),
                                                    sha256=os.environ['AUTODRIVER_CONTRACT_BUNDLE_SHA256']))
            self.contract_bundle=json.loads(data)
            if self.contract_bundle.get('variant','full')!=variant_name:raise ValueError('contract bundle intervention differs')
        checkpoint_path=os.environ.get('AUTODRIVER_FP_CHECKPOINT')
        if checkpoint_path:
            path=Path(checkpoint_path)
            if path.parent!=self.workspace.root/'output' or path.is_symlink():
                raise ValueError('FP checkpoint must be a host-prepared output file')
            data=read_pinned(self.workspace.root,dict(id='host-fp-checkpoint',path=path.relative_to(self.workspace.root).as_posix(),
                                                    sha256=os.environ['AUTODRIVER_FP_CHECKPOINT_SHA256']))
            checkpoint=json.loads(data)
            if checkpoint['task_id']!=self.workspace.task['task_id'] or checkpoint['input_identity']!=self.workspace.manifest['identity']:
                raise ValueError('FP checkpoint belongs to a different prepared input')
            self.fp_checkpoints=checkpoint['profiles']
            self.fp_seeds=checkpoint.get('seeds',{})
        stage_path=os.environ.get('AUTODRIVER_STAGE_CHECKPOINT')
        if stage_path:
            path=Path(stage_path)
            if path.parent!=self.workspace.root/'output' or path.is_symlink():raise ValueError('stage checkpoint must be host prepared')
            data=read_pinned(self.workspace.root,dict(id='host-stage-checkpoint',path=path.relative_to(self.workspace.root).as_posix(),
                                                    sha256=os.environ['AUTODRIVER_STAGE_CHECKPOINT_SHA256']))
            checkpoint=json.loads(data)
            if checkpoint['task_id']!=self.workspace.task['task_id'] or checkpoint['input_identity']!=self.workspace.manifest['identity']:
                raise ValueError('stage checkpoint prepared input changed')
            self.runner.saved_responses=checkpoint['responses'];self.context_checkpoints=checkpoint['contexts']

    def request(self,stage,selections,goal,**extra):
        self.version+=1
        if stage in {'fp_build','dbc_recover','bp_build'} or stage=='repair' and extra.get('repair_target') in {'fp','dbc','bp'}:
            extra['contract_transport']='constructor_tree'
        return dict(schema='autodriver.llm-call/v0.1',call_id=f'call-{self.version:03d}-{stage}',stage=stage,
                    condition='FRAMEWORK' if stage=='fp_build' or stage=='repair' and extra.get('repair_target')=='fp' else 'SD',goal=goal,selections=selections,
                    max_context_bytes=self.max_context_bytes,**extra)

    def update_evidence(self):self.runner.evidence=self.workspace.evidence()

    def write_call_material(self,*args,**kwargs):
        id=self.workspace.add_derived(*args,**kwargs)
        return dict(self.workspace.entries[id])

    def select(self):
        for role in ('source','target'):
            ids,scope,audit=select_framework(self.workspace,role)
            self.framework_ids[role]=ids;self.framework_scope[role]=scope
            self.framework_selections[role]=[dict(item,role='framework_evidence') for item in audit['selections']]
            write_private_json(self.output/(role+'-framework-selection.json'),audit)
        self.update_evidence()
        if self.contract_bundle is not None:
            self.saved_objects,self.import_audit=import_bundle(self.workspace,self.contract_bundle)
            self.update_evidence();write_private_json(self.output/'contract-import.json',self.import_audit)
        from .framework_pack import import_pack
        for role,pack in self.framework_packs.items():
            key=role+'_fp'
            if key in self.saved_objects:raise ValueError('framework and task-local imports cannot overwrite one another')
            saved,audit=import_pack(self.workspace,pack,role,self.framework_scope[role]);self.saved_objects[key]=saved
            if self.import_audit is None:self.import_audit=audit
            write_private_json(self.output/(role+'-framework-pack-import.json'),audit)
        self.update_evidence()

    def adopt_saved(self,key):
        saved=self.saved_objects[key]
        if saved.get('seed_only'):raise ValueError('protocol-rejected saved object is a repair seed, never an accepted contract')
        parents={name:self.revisions.current[name] for name in saved['parents'] if name in self.revisions.current}
        revision=self.revisions.add(key,saved['artifact'],parents,provenance={'producer':'verified_saved_contract_import',
            'origin':saved['origin'],'import_bundle_sha256':saved['bundle_sha256'],'framework_version':saved.get('framework_version')})
        self.discard_invalidated()
        self.artifacts[key]=saved['artifact'];self.material_ids[key]=saved['material_id'];self.checks[key]=saved['checks']
        self.evidence_reviews[key]=bool(saved.get('evidence_refined',False))
        self.workspace.roles[saved['material_id']]={'source_fp':'source_fp','target_fp':'target_fp','dbc':'dbc','bp':'bp'}[key]
        self.runner.calls.append({'call_id':'artifact-import-'+key,'stage':'artifact_import','status':'rechecked',
            'execution_mode':'verified_saved_artifact_import','new_upstream_requests':0,'origin':saved['origin']})
        write_private_json(self.output/(key+'-artifact-import.json'),{'revision':revision.identity,'checks':saved['checks'],
            'origin':saved['origin'],'new_upstream_requests':0})
        self.export_state()
        return True

    def saved_parents_current(self,saved):
        for key,id in saved['parents'].items():
            if key not in self.artifacts:return False
            actual=saved['parent_materials'].get(key)
            if actual is None:return False
            kind='fp' if key.endswith('_fp') else key
            if semantic_identity(self.workspace,self.workspace.evidence().read(actual).decode(),kind)!=semantic_identity(self.workspace,self.artifacts[key]['content'],kind):
                return False
        return True

    def discard_invalidated(self):
        """Keep public history, remove superseded current-object pointers."""
        stale=set(self.artifacts)-set(self.revisions.current)
        for key in stale:
            for values in (self.artifacts,self.material_ids,self.checks,self.responses,self.evidence_reviews):values.pop(key,None)
            self.saved_objects.pop(key,None)
            if key=='bp':self.checks.pop('binding',None)
            if key=='code':self.code_proposal=None
        if stale:
            self.checks.pop('generation_admission',None);self.checks.pop('recovery_support',None)

    def export_state(self):
        materials=[]
        for id,entry in self.workspace.entries.items():
            item={key:entry[key] for key in ('id','kind','approved','os_role','sha256','parents')}
            if entry.get('producer'):item['producer']=entry['producer']
            if entry['path'] in self.workspace.manifest['files']:item['original_path']=entry['path']
            else:item['text']=self.workspace.evidence().read(id).decode('utf8')
            materials.append(item)
        objects={key:{'material_id':self.material_ids[key],'revision':self.revisions.current[key],
            'response':self.responses.get(key),'imported_origin':self.saved_objects.get(key,{}).get('origin')}
            for key in self.artifacts}
        write_private_json(self.output/'contract-state.json',{'schema':'autodriver.contract-state/v1',
            'task_id':self.workspace.task['task_id'],'input_identity':self.workspace.manifest['identity'],
            'variant':self.variant_name,'objects':objects,'materials':materials,'checks':self.checks,
            'scope':'source/contract data only; old checks are records and must be recomputed on import'})

    def fp(self,role):
        key=role+'_fp'
        semantic_repair=None
        if key in self.saved_objects:
            saved=self.saved_objects[key];root=saved['root'];snapshot=root['snapshot']
            expected_os=self.workspace.task['source' if role=='source' else 'target']['os']
            current_scope=not saved.get('seed_only') and self.fp_scope_current(root,role)
            if current_scope:
                if saved['checks']['status']!='violated':return self.adopt_saved(key)
                semantic_repair=saved['checks']
            # Keep the old object as a seed, never as current-scope assurance.
            response={'artifact':saved['artifact']}
            self.fp_seeds[role]={'response':response,'response_sha256':digest(canonical(response).encode()),
                'origin_request_id':saved['origin']['request_id'],'host_receipt_sha256':saved['origin']['receipt_sha256']}
        scope_id=(saved['root']['snapshot']['identifier'] if semantic_repair is not None
                  and self.fp_scope_current(saved['root'],role) else self.framework_scope[role])
        selected=[{'role':'method_specification','material_id':'method'},
                  {'role':'framework_context','material_id':scope_id}]
        selected += self.framework_selections[role]
        expected_os=self.workspace.task['source' if role=='source' else 'target']['os']
        goal='恢复当前获准 OS 快照和配置能力范围的框架语义。候选 provider 不代表已证明选中；只保留原文支持的保证、适用前提与明确缺口。根 os='+repr(expected_os)+'，snapshot=Ref('+repr(self.framework_scope[role])+')；这个材料固定了本次框架源码身份和能力范围。'
        goal+=(' 必要原生 ABI 从声明、字段类型和注释分别恢复。保留已知 unit/scale/offset/存储类型，未知位宽、合法range和精度分别保留Unspecified，不能因一个字段未知而抹掉整个OutputEncoding。'
               'C浮点只有格式/配置依据时用f32/f64；未定宽usize可忠实记录为storage_type并保留宽度缺口，不可冒充已验证数值范围。'
               '每条evidence_link必须完全位于本次单个context片段的[start_line,end_line]，不要跨未提供行段；不同片段使用不同link。')
        goal+=(' 按当前冻结配置及所需能力恢复事实，区分接口承诺、公开板级类型/构造证据与尚未建立的实际运行或物理保证；'
               '不要把当前配置的有据事实扩大为所有provider的普遍保证，也不要因其它provider未知而抹掉当前来源事实。'
               '不适用的资源/错误渠道应依据原始签名用字段允许的None或空集合明确表示；缺少证据仍用Unspecified，不得假装不适用。'
               'Constraint.evidence必须逐字匹配本响应evidence_links中的claim文本，不能填material_id或citation句柄。'
               '全局unresolved只列当前所需范围仍未恢复的义务；其它provider的局部不确定性放在相应条目，不要求为整个OS所有provider建立统一保证。'
               '可以用capabilities分别保留各provider有据的契约；不要用不可解析的自然语言条件冒充已编码的provider选择。'
               '原始类型/enum/板级配置事实不是一次native API调用，不能为它们虚构APIContract的accepted/rejected/completion义务；使用相应字段允许的普通结构化值。')
        if role in self.fp_seeds:
            seed=self.fp_seeds[role];response=seed['response']
            if seed['response_sha256']!=digest(canonical(response).encode()):raise ValueError('FP seed changed')
            content=response['artifact']['content']
            try:prior=parse(content,'fp');prior_os=prior['os']
            except ContractError:
                if not seed.get('rejected'):raise
                prior_os=seed['os']
            if prior_os!=expected_os:raise ValueError('FP seed is for a different OS')
            if seed.get('rejected') and seed['scope_sha256']==self.workspace.entries[self.framework_scope[role]]['sha256']:
                raise ValueError('exhausted rejected FP requires expanded evidence, never an unchanged retry')
            id=role+'.fp.seed.'+seed['response_sha256'][:16]
            self.workspace.add_derived(id,id+'.constructor','fp_task_local','existing_fp',content+'\n',
                [self.framework_scope[role]],os_role=role)
            self.update_evidence();selected.append({'role':'candidate' if semantic_repair else 'existing_fp','material_id':id})
            write_private_json(self.output/(role+'-fp-seed-origin.json'),{
                'origin_request_id':seed['origin_request_id'],'host_receipt_sha256':seed['host_receipt_sha256'],
                'response_sha256':seed['response_sha256'],'classification':'old partial candidate, not an assurance for the current scope'})
            goal+=(' 已有 FP 仍是未验证草稿。按当前原文和诊断复查，只保留有据保证；不能直接承认旧保证。'
                   '必要 API 的 accepted/rejected/completion/resource_effect 请使用有原文依据的可执行类型节点，'
                   '如 HoldsBufferUntilCompletion、ReturnsErrorAndBuffer、Callback(resource_effect=ReturnsBufferToClient())；'
                   '自然语言可以解释，但不能替代这些已恢复的效果。没有依据的效果保持 Unspecified。'
                   '依据机械 enclosing 信息区分 trait/impl/模块，测试专用声明不属于当前非测试构建。'
                   'Cargo 路径图只是依赖候选，不证明具体运行时实例；按实际证据分别恢复总线完成关系、计数器频率/位宽/回绕与原生输出编码。')
        if semantic_repair:
            id=role+'.fp.semantic-diagnostic'
            self.workspace.add_derived(id,id+'.json','internal_diagnostic','internal_diagnostic',canonical(semantic_repair)+'\n',
                [selected[-1]['material_id']],os_role=role,producer='method_contract_checker')
            selected.append({'role':'internal_diagnostic','material_id':id});self.update_evidence()
        request=self.request('repair' if semantic_repair else 'fp_build',selected,goal,
                             **({'repair_target':'fp'} if semantic_repair else {}))
        if role in self.fp_checkpoints:
            checkpoint=self.fp_checkpoints[role]
            if checkpoint['response_sha256']!=digest(canonical(checkpoint['response']).encode()):
                raise ValueError('FP checkpoint response changed')
            contexts=json.loads(checkpoint['messages'][-1]['content'])['context']
            for context in contexts:
                if context['kind'] not in {'framework_source','framework_scope','method_specification'}:continue
                id=context['material_id']
                if id not in self.workspace.entries or self.workspace.entries[id]['sha256']!=context['sha256']:
                    raise ValueError('FP checkpoint framework/configuration scope changed: '+id)
                self.workspace.evidence().read(id)
            checked=check_response(checkpoint['response'],checkpoint['request'],checkpoint['messages'])
            response=checkpoint['response'];fp=parse(response['artifact']['content'],'fp')
            if not self.fp_scope_current(fp,role):
                raise ValueError('FP checkpoint OS/scope changed')
            self.runner.calls.append(dict(call_id='checkpoint-'+role+'-fp',stage='fp_build',status='response_checked',
                execution_mode='host_receipt_checkpoint_local_validation',origin_request_id=checkpoint['origin_request_id'],
                semantic_status=checked['semantic_validation']['status'],new_upstream_requests=0))
            write_private_json(self.output/(role+'-fp-checkpoint-import.json'),{
                'origin_request_id':checkpoint['origin_request_id'],'host_receipt_sha256':checkpoint['host_receipt_sha256'],
                'response_sha256':checkpoint['response_sha256'],'new_upstream_requests':0,'kind':'task_local_development_checkpoint'})
            accepted=self.accept(role+'_fp',response,checked,selected,{})
            return accepted and checked['semantic_validation']['status']!='violated'
        saved=None
        if role=='source' and os.environ.get('AUTODRIVER_SAVED_FP_RESPONSE'):
            saved_path=Path(os.environ['AUTODRIVER_SAVED_FP_RESPONSE'])
            if saved_path.is_symlink() or not saved_path.is_relative_to(self.workspace.root/'output'):
                raise ValueError('saved response must be a host-prepared regular continuation file')
            saved=json.loads(saved_path.read_text())
        response,checks,_=self.runner.recover(request,saved_response=saved)
        if response['artifact'] is not None:
            fp=parse(response['artifact']['content'],'fp')
            expected_os=self.workspace.task['source' if role=='source' else 'target']['os']
            snapshot=fp['snapshot']
            if not self.fp_scope_current(fp,role):
                checks['semantic_validation']['status']='violated'
                checks['semantic_validation'].setdefault('findings',[]).append({'subject':'snapshot','status':'violated',
                    'check':'os_and_snapshot_identity','detail':'FP must use the supplied OS and exact framework_scope Ref'})
        accepted=self.accept(role+'_fp',response,checks,selected,{})
        return accepted and checks['semantic_validation']['status']!='violated'

    def fp_scope_current(self,root,role):
        """Aliases may differ; the complete approved scope bytes may not."""
        snapshot=root['snapshot']
        if root['os']!=self.workspace.task[role]['os'] or getattr(snapshot,'name',None)!='Ref':return False
        actual=self.workspace.entries.get(snapshot['identifier'],{})
        expected=self.workspace.entries[self.framework_scope[role]]
        if actual.get('kind')!='framework_scope' or actual.get('os_role')!=role or actual.get('sha256')!=expected['sha256']:return False
        # Checking registry metadata alone cannot admit a changed imported file.
        return self.workspace.evidence().read(snapshot['identifier'])==self.workspace.evidence().read(self.framework_scope[role])

    def accept(self,key,response,checks,selections,parents):
        if self.runner.last_request and response['call_id']==self.runner.last_request['call_id']:
            selections=self.runner.last_request['selections']
        if key.endswith('_fp') and response['artifact'] is not None:
            role=key.removesuffix('_fp');root=parse(response['artifact']['content'],'fp');snapshot=root['snapshot']
            if not self.fp_scope_current(root,role):
                checks['semantic_validation']['status']='violated'
                checks['semantic_validation'].setdefault('findings',[]).append({'subject':'snapshot','status':'violated',
                    'check':'os_and_snapshot_identity','detail':'FP must use the current OS and exact complete approved framework scope bytes, including semantic repairs'})
        self.checks[key]=checks['semantic_validation']
        if response['artifact'] is None:return False
        artifact=response['artifact'];revision=self.revisions.add(key,artifact,parents,provenance={
            'call_id':response['call_id'],'response_sha256':digest(canonical(response).encode()),
            'material_parents':{s['material_id']:self.workspace.entries[s['material_id']]['sha256'] for s in selections}})
        self.discard_invalidated()
        self.artifacts[key]=artifact
        self.responses[key]=response
        self.evidence_reviews[key]=bool(key=='dbc' and self.runner.last_request
            and self.runner.last_request['call_id']==response['call_id'] and self.runner.last_request.get('dbc_review')
            and self.policy.dbc_evidence_refinement)
        id=key+'.'+revision.identity[:16]
        material_kind='fp_task_local' if key.endswith('_fp') else key+'_candidate'
        role={'source_fp':'source_fp','target_fp':'target_fp','dbc':'dbc','bp':'bp'}[key]
        self.workspace.add_derived(id,key+'-'+revision.identity+'.constructor',material_kind,role,artifact['content']+'\n',
                                   tuple(dict.fromkeys(s['material_id'] for s in selections)),
                                   os_role='source' if key=='source_fp' else 'target' if key in {'target_fp','bp'} else 'shared')
        self.material_ids[key]=id;self.update_evidence()
        write_private_json(self.output/(key+'-response.json'),response)
        self.export_state()
        return True

    def review_source_selections(self,selected,facts,serialized):
        """Question-sized index; all originals stay in the approved query pool."""
        if len(serialized.encode())<=65536:return list(selected)
        from .context_tables import compact
        graph=facts['program_dependencies']
        index={'schema':'autodriver.source-review-index/v1','complete_analysis_sha256':digest(serialized.encode()),
            'functions':graph['functions'],'calls':graph['calls'],'configuration':graph['configuration'],
            'gaps':graph['gaps'],'excluded':graph['excluded'],'syntax_gaps':facts['gaps'],
            'build_membership':facts['build_membership'],'source_api_retrieval':facts['source_api_retrieval'],
            'scope':'all raw source/manuals remain in the approved frozen query pool; the actual supplied ranges are listed in the current message. This is the full function/call index and all analysis gaps, not the full control/data graph or a proof.',
            'retrieve_complete_function_graph':{'context_requests':[{'scope':'internal_tools','question':'state the specific unresolved source effect',
                'query':'{"tool":"slice_device_effects","arguments":{"roots":["exact function name from functions"]}}',
                'reason':'retrieve whole function/callee control-data closure; no branch or dependency truncation'}]}}
        id='source.review-index'
        self.workspace.add_derived(id,id+'.json','source_analysis','source_facts',canonical(compact(index))+'\n',['source.syntax'],os_role='source')
        self.update_evidence()
        return [item for item in selected if item['role']!='source_facts']+[{'role':'source_facts','material_id':id}]

    def dbc(self):
        saved=self.saved_objects.get('dbc')
        if (saved and not saved.get('seed_only') and saved['reviewed_task_sha256']==self.workspace.entries['task']['sha256'] and self.saved_parents_current(saved)
                and saved['checks']['status']!='violated' and (not self.policy.dbc_evidence_refinement or saved.get('evidence_refined'))):
            return self.adopt_saved('dbc')
        if saved and not self.policy.dbc_evidence_refinement:
            self.checks['dbc_checkpoint']={'status':'unknown','reason':'current public-task projection changed while DBC refinement is disabled'}
            return False
        selected=self.workspace.selections({'method_specification','task_context','source_configuration','source_evidence','document_evidence'})
        if self.policy.source_framework_lifting:
            selected.append({'role':'source_fp','material_id':self.material_ids['source_fp']})
        # Same approved source framework originals remain available to both
        # lifting and no-lifting conditions; no structured effect summary leaks.
        index=SourceIndex(self.workspace.evidence())
        for id,entry in self.workspace.entries.items():
            if entry['kind']=='device_source' and Path(entry['path']).suffix in {'.c','.h','.rs'}:index.add(id)
        facts={'scope':'syntax facts; active configuration and API device effects still require recovery',
               'symbols':[{k:v for k,v in vars(sym).items() if k!='enclosing' or v} for sym in index.symbols.values()],'gaps':index.gaps}
        dependencies=CAnalysis(self.workspace.evidence(),self.workspace.macro_configuration())
        membership=self.workspace.source_membership()
        for id,entry in self.workspace.entries.items():
            if entry['kind']=='device_source' and Path(entry['path']).suffix in {'.c','.h'}:
                records=[record for record in membership if record['path']==entry['path']]
                if records and all(record['active'] is False for record in records):
                    dependencies.excluded.append({'material_id':id,'sha256':entry['sha256'],'build_membership':records,
                                                  'reason':'source translation unit excluded by frozen build configuration'})
                else:dependencies.add(id)
        facts['program_dependencies']=dependencies.report()
        facts['build_membership']=membership
        # DBC receives complete declarations relevant to the actual source
        # call graph. The full approved framework pool is unchanged and remains
        # available through tools in both lifting and no-lifting conditions.
        framework_index=SourceIndex(self.workspace.evidence())
        for id in self.framework_ids['source']:
            if self.workspace.entries[id]['kind']=='framework_source':framework_index.add(id)
        external=sorted({call['callee'] for call in facts['program_dependencies']['calls'] if not call['targets']})
        api_roots=[name for name in external if name in framework_index.by_name]
        excerpts=framework_index.excerpts(api_roots,maximum_lines=6000) if api_roots else {'selections':[],'gaps':[]}
        selected += [dict(item,role='framework_evidence') for item in excerpts['selections']]
        facts['source_api_retrieval']={'roots':api_roots,'gaps':excerpts['gaps'],
            'unresolved_external_calls':[name for name in external if name not in framework_index.by_name],
            'scope':'complete indexed API declarations/call/type closure; unresolved effects require evidence recovery'}
        serialized=json.dumps(facts,ensure_ascii=False,separators=(',',':'))+'\n'
        if 'source.syntax' in self.context_checkpoints:
            prior=self.context_checkpoints['source.syntax']
            def normalized_facts(data):
                data=json.loads(data)
                data['program_dependencies']['dependencies'].sort(key=lambda e:json.dumps(e,sort_keys=True))
                return data
            if digest(prior['text'].encode())!=prior['sha256'] or normalized_facts(prior['text'])!=normalized_facts(serialized):
                raise ValueError('frozen source analysis facts changed; cached DBC must not be silently reused')
            serialized=prior['text']
            write_private_json(self.output/'source-facts-local-reuse.json',{'scope':'same complete analysis facts; only dependency-set iteration order normalized',
                'old_text_sha256':prior['sha256'],'current_analysis_sha256':digest(json.dumps(facts,ensure_ascii=False,separators=(',',':')).encode()),
                'new_upstream_requests':0})
        source_parents=[id for id,e in self.workspace.entries.items() if e['kind']=='device_source']
        source_parents += [id for id,e in self.workspace.entries.items() if e['kind']=='source_configuration']
        # Lossless compact serialization keeps the same complete dependency
        # facts in the prompt; it never clips source or drops analysis records.
        self.workspace.add_derived('source.syntax','source-syntax.json','source_analysis','source_facts',serialized,source_parents,os_role='source')
        # Keep the full analysis. For large inputs select the complete control/
        # data graph and its gaps; the redundant syntax symbol index stays in
        # the parent and is queryable from the same original source. Raw source
        # remains fully selected. Encode this view losslessly, without clipping
        # dependencies, dropping failure branches, or raising the common bound.
        facts_id='source.syntax'
        if len(serialized.encode())>65536:
            from .context_tables import compact,expand
            view={key:value for key,value in json.loads(serialized).items() if key!='symbols'}
            view['syntax_index']={'symbol_count':len(facts['symbols']),'full_analysis_sha256':digest(serialized.encode()),
                'retrieval':'inspect_symbol with scope=source queries the same complete original source; no source files are omitted',
                'selected_semantics':'complete program_dependencies, source API retrieval, configuration, build membership and every analysis gap'}
            packed=compact(view)
            if expand(packed)!=view:raise ValueError('source facts table changed selected analysis')
            encoded=canonical(packed)+'\n'
            if len(encoded.encode())<len(serialized.encode()):
                facts_id='source.syntax.table'
                self.workspace.add_derived(facts_id,'source-syntax-table.json','source_analysis','source_facts',encoded,['source.syntax'],os_role='source')
        self.update_evidence();selected.append({'role':'source_facts','material_id':facts_id})
        if saved:
            selected=self.review_source_selections(selected,facts,serialized)
            id='dbc.import-review'
            diagnostic={'previous_checks':dict(saved['checks'],source_response_protocol_error=saved.get('source_response_protocol_error')),'old_public_task_sha256':saved['reviewed_task_sha256'],
                'current_public_task_sha256':self.workspace.entries['task']['sha256'],
                'questions':['复查当前公开集成原文中的产品变体、初始化前提、采集新鲜度、失败和时间界；不能从源码习惯发明保持义务',
                             '核对每个事务的全部器件输出字节、读写头和dummy接收分段，寄存器width为位数；ReadFrame不隐含额外命令，BP不能发明遗漏的器件字节',
                             '规范物理公式属于DBC，不得把未展开公式推给BP。中间变量必须绑定当前有效帧、显式校准数据或声明的FSM变量/Assign；未定义符号不能当完整公式',
                             '优先只编辑诊断所指字段；已有封闭的命名中间公式和值不必重写。不能用自然语言、源函数名或未声明缩写代替规范公式；requires也须是实际绑定值上的纯谓词，未知前提继续报告',
                             '初始化读取的旧帧不会自动成为新采集可用输入。若原源码持久保存校准/有效性，应有据恢复显式FSM状态与Assign效果；不发明真值、变量域或生命周期',
                             '原契约仅作候选，结合当前同一冻结原始资料和新增可见的公开要求核对完整全局FSM及来源；明确未决并保留约束',
                             '不能改FP或设备策略；这次只修DBC，不重新生成已有FP']}
            self.workspace.add_derived(id,id+'.json','internal_diagnostic','internal_diagnostic',json.dumps(diagnostic,ensure_ascii=False,indent=2)+'\n',
                                      [saved['material_id'],'task'],producer='method_contract_checker')
            self.update_evidence();selected += [{'role':'candidate','material_id':saved['material_id']},{'role':'internal_diagnostic','material_id':id}]
            request=self.request('repair',selected,'仅修订已保存的DBC：核对当前公开任务/集成及冻结源码—手册，恢复并检查一份全局模型。完整原文未改变，旧候选不必重建；不得降低约束或虚构设备/框架保证。',repair_target='dbc',dbc_review=True)
        else:request=self.request('dbc_recover',selected,'从当前冻结源、配置和原始器件资料自动恢复一份全局设备行为契约。区分源 API 效应、设备要求与任务保持义务；不得使用任何人工传感器答案。')
        response,checks,_=self.runner.recover(request)
        parents={'source_fp':self.revisions.current['source_fp']} if self.policy.source_framework_lifting else {}
        if not self.accept('dbc',response,checks,selected,parents):return False
        if saved:return checks['semantic_validation']['status']!='violated'  # This explicit repair already performs the dedicated review.
        if not self.policy.dbc_evidence_refinement:return checks['semantic_validation']['status']!='violated'
        diagnostic_id='dbc.review-diagnostic'
        diagnostic={'semantic_checks':self.checks['dbc'],'questions':[
            '逐项核对源码与手册对当前策略、初始化、事务边界、时间条件、布局、完整性、补偿和提交的依据',
            '核对每个总线事务的全部输出字节和寄存器读写头、dummy、帧接收范围；width为位数，ReadFrame不隐含额外器件命令，缺失的器件字节不能留给BP发明',
            '规范物理公式属于DBC，不得把未展开公式推给BP。中间变量必须绑定当前有效帧、显式校准数据或声明的FSM变量/Assign；未定义符号不能当完整公式',
            '补齐遗漏的初始化、失败、恢复和重入路径；未知保留，不能增加默认重试或伪造新成功',
            '产品时间上界和启用/失败/新鲜度要求独立于目标provider保证；用带本次逻辑事件锚点的Deadline保留可执行上界，不能只写自然语言或因FP未知而删除',
            '源延时常量不是自动保持义务；核对器件条件下合法物理下界并记录冲突，不把源实现缺陷冻结成要求',
            '检查全局可达性、物理事件/本次身份关联和必要终态；只引用本次实际提供证据',
            '区分已检查事实、模型辅助证据核对和未解决语义，不把引用存在当成蕴含证明']}
        self.workspace.add_derived(diagnostic_id,'dbc-review-diagnostic.json','internal_diagnostic','internal_diagnostic',
                                   json.dumps(diagnostic,ensure_ascii=False,indent=2)+'\n',[self.material_ids['dbc']],producer='method_contract_checker')
        refinement=self.review_source_selections(selected,facts,serialized)+[{'role':'candidate','material_id':self.material_ids['dbc']},
                                  {'role':'internal_diagnostic','material_id':diagnostic_id}]
        self.update_evidence()
        request=self.request('repair',refinement,'仅修订 DBC：执行独立的源码—手册核对和必要补证，复查一份全局 FSM。设备策略和原始资料只读；保留所有仍未解决的冲突及覆盖。',repair_target='dbc',dbc_review=True)
        response,checks,_=self.runner.recover(request)
        accepted=self.accept('dbc',response,checks,refinement,parents)
        return accepted and checks['semantic_validation']['status']!='violated'

    def bp(self):
        support=self.workspace.task['target']['interface']
        public_support_ids=[id for id,e in self.workspace.entries.items() if e['kind']=='public_target_support']
        if 'integration' not in self.workspace.entries:
            self.workspace.add_derived('integration','integration.json','public_target_support','target_support',
                                       json.dumps(support,ensure_ascii=False,indent=2)+'\n',['task',*public_support_ids],os_role='target')
        elif json.loads(self.workspace.evidence().read('integration'))!=support:
            raise ValueError('read-only public integration changed while rebuilding BP')
        self.update_evidence()
        selected=self.workspace.selections({'method_specification','task_context','target_support'})
        selected += [{'role':'dbc','material_id':self.material_ids['dbc']},{'role':'target_fp','material_id':self.material_ids['target_fp']}]
        base_selected=list(selected)
        saved=self.saved_objects.get('bp')
        if saved and not saved.get('seed_only') and saved['reviewed_task_sha256']==self.workspace.entries['task']['sha256'] and self.saved_parents_current(saved):
            root=parse(saved['artifact']['content'],'bp');names={root[key]['identifier']:self.material_ids[role]
                for key,role in [('device_contract','dbc'),('framework_profile','target_fp')]}
            old_support=root['target_support']['identifier']
            if self.workspace.entries[old_support]['sha256']==self.workspace.entries['integration']['sha256']:
                names[old_support]='integration'
                content,changes=rebase_constructor(saved['artifact']['content'],'bp',names)
                saved['artifact']=dict(saved['artifact'],content=content)
                # Register the locally rebound alias as a new derived value;
                # the original imported material remains immutable.
                id='bp.local-parent-aliases'
                self.workspace.add_derived(id,id+'.constructor','bp_candidate','bp',content+'\n',
                    [saved['material_id'],self.material_ids['dbc'],self.material_ids['target_fp'],'integration'])
                saved['material_id']=id;self.update_evidence()
                old_checks_sha256=digest(canonical(saved['checks']).encode())
                # Alias rebasing changes the canonical contract bytes. Re-run
                # every semantic check against the actual rebound references;
                # the old report remains provenance, never current assurance.
                root,checks=check_contract(content,'bp',references=references_for(self.workspace),
                    evidence_claims=[link['claim'] for link in saved.get('evidence_links',[])])
                saved['root']=root
                saved['checks']=include_response_gaps(checks,saved.get('unresolved',[]))
                self.adopt_saved('bp')
                write_private_json(self.output/'bp-local-parent-aliases.json',{'changes':changes,'new_upstream_requests':0,
                    'old_checks_sha256':old_checks_sha256,'current_checks_sha256':digest(canonical(saved['checks']).encode()),
                    'semantic_checks_recomputed':True})
                self.check_saved_binding(support)
                if self.checks['bp']['status']!='violated' and self.checks['binding']['status']!='violated':return True
        goal='构建只读设备行为契约到目标框架的绑定，并派生时间、数据、资源和原生请求终态义务；不能重写设备事实或框架保证。根的三个父 Ref 使用这些实际材料 ID：DBC='+self.material_ids['dbc']+'，FP='+self.material_ids['target_fp']+'，公开集成=integration。'
        if saved:
            id='bp.import-review'
            diagnostic={'checks':saved['checks'],'composition_checks':self.checks.get('binding'),
                'paths':binding_inventory(parse(self.artifacts['dbc']['content'],'dbc'),parse(self.artifacts['target_fp']['content'],'fp'),support),
                'instruction':'Recheck the saved BP against current parent IDs, paths and deterministic composition checks. Repair only BP. Preserve all genuine gaps; an imported plan is not automatically valid.'}
            self.workspace.add_derived(id,id+'.json','internal_diagnostic','internal_diagnostic',json.dumps(diagnostic,ensure_ascii=False,indent=2)+'\n',
                [saved['material_id'],self.material_ids['dbc'],self.material_ids['target_fp']],producer='method_binding_checker')
            self.update_evidence();selected += [{'role':'candidate','material_id':saved['material_id']},{'role':'internal_diagnostic','material_id':id}]
            request=self.request('repair',selected,'仅修订已保存BP以绑定当前只读父版本。'+goal,repair_target='bp')
        else:request=self.request('bp_build',selected,goal)
        response,checks,_=self.runner.recover(request)
        parents={key:self.revisions.current[key] for key in ('dbc','target_fp')}
        if not self.accept('bp',response,checks,selected,parents):return False
        dbc=parse(self.artifacts['dbc']['content'],'dbc');fp=parse(self.artifacts['target_fp']['content'],'fp')
        # Aggregate public support values are a projection of the supplied
        # native interface only. No host reference/configuration is consulted.
        cycle=self.version
        for attempt in range(3):
            bp=parse(self.artifacts['bp']['content'],'bp')
            report=check_binding(dbc,fp,bp,support,parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity(support)},
                                 parent_ids={'dbc':self.material_ids['dbc'],'fp':self.material_ids['target_fp'],'support':'integration'})
            write_private_json(self.output/('binding-checks-'+str(cycle)+'-'+str(attempt)+'.json'),report)
            write_private_json(self.output/'binding-checks.json',report);self.checks['binding']=report
            if report['status']!='violated' or attempt==2:break
            id='bp.binding-diagnostic.'+str(cycle)+'.'+str(attempt)
            diagnostic={'checks':report,'paths':binding_inventory(dbc,fp,support),
                        'instruction':'Only BP may change. Use exact supplied paths or a defined generic primitive binding. Missing parent guarantees remain unresolved; never invent integration fields, provider guarantees or relax the DBC.'}
            self.workspace.add_derived(id,id+'.json','internal_diagnostic','internal_diagnostic',
                json.dumps(diagnostic,ensure_ascii=False,indent=2)+'\n',[self.material_ids['bp']],producer='method_binding_checker')
            self.update_evidence()
            repair=list(base_selected)+[{'role':'candidate','material_id':self.material_ids['bp']},{'role':'internal_diagnostic','material_id':id}]
            request=self.request('repair',repair,'仅修复 BP 的确定性映射/路径错误。诊断提供当前父对象的精确路径；保留所有上游真实缺口，不修改 DBC/FP，不把缺口写成保证。',repair_target='bp')
            response,checks,_=self.runner.recover(request)
            if not self.accept('bp',response,checks,repair,parents):return False
        self.export_state()
        return True

    def check_saved_binding(self,support):
        dbc=parse(self.artifacts['dbc']['content'],'dbc');fp=parse(self.artifacts['target_fp']['content'],'fp');bp=parse(self.artifacts['bp']['content'],'bp')
        report=check_binding(dbc,fp,bp,support,parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity(support)},
            parent_ids={'dbc':self.material_ids['dbc'],'fp':self.material_ids['target_fp'],'support':'integration'})
        self.checks['binding']=report;write_private_json(self.output/'binding-checks.json',report);self.export_state()
        return True

    def assess_generation(self):
        keys=['dbc','target_fp']+(['bp'] if self.policy.binding_plan else [])
        contracts={key:parse(self.artifacts[key]['content'],'fp' if key=='target_fp' else key) for key in keys}
        support_audits={}
        for key,root in contracts.items():
            links=self.responses.get(key,self.saved_objects.get(key,{})).get('evidence_links',[])
            support_audits[key]=audit_support(root,'fp' if key=='target_fp' else key,links,self.workspace.evidence(),
                evidence_refined=self.evidence_reviews.get(key,False),
                refinement_required=key=='dbc' and self.policy.dbc_evidence_refinement)
        self.checks['recovery_support']=support_audits
        admission=generation_admission(self.checks,binding_plan=self.policy.binding_plan,contracts=contracts,support_audits=support_audits)
        self.checks['generation_admission']=admission
        write_private_json(self.output/'generation-admission-checks.json',admission)
        return admission

    def refine_contract(self,key,diagnostic):
        """A single object-specific upstream repair, with original evidence.

        This is a bounded semantic attempt, not promotion of an unknown. Parent
        changes invalidate current descendants, which must be rebuilt normally.
        """
        if key not in {'dbc','target_fp','bp'}:raise ValueError('unsupported upstream repair object')
        if key=='dbc' and not self.policy.dbc_evidence_refinement:raise ValueError('DBC refinement is disabled')
        if key=='bp' and not self.policy.binding_plan:raise ValueError('BP is disabled')
        if key not in self.artifacts:raise ValueError('upstream repair requires its current candidate')
        if key=='target_fp':
            root=parse(self.artifacts[key]['content'],'fp')
            scope_id=root['snapshot']['identifier'] if self.fp_scope_current(root,'target') else self.framework_scope['target']
            selected=[{'role':'method_specification','material_id':'method'},
                      {'role':'framework_context','material_id':scope_id}]
            selected+=self.framework_selections['target']
            parents={};kind='fp'
            # Framework recovery stays task/device independent. A combined
            # admission report can contain device facts and is never its input.
            diagnostic={'framework_checks':self.checks['target_fp'],
                        'support_audit':self.checks.get('recovery_support',{}).get('target_fp')}
        elif key=='dbc':
            selected=self.workspace.selections({'method_specification','task_context','source_configuration','source_evidence','document_evidence'})
            selected+=self.framework_selections['source']
            if self.policy.source_framework_lifting:selected.append({'role':'source_fp','material_id':self.material_ids['source_fp']})
            parents={'source_fp':self.revisions.current['source_fp']} if self.policy.source_framework_lifting else {}
            kind='dbc'
        else:
            selected=self.workspace.selections({'method_specification','task_context','target_support'})
            selected+=self.framework_selections['target']
            selected += [{'role':name,'material_id':self.material_ids[name]} for name in ('dbc','target_fp')]
            parents={name:self.revisions.current[name] for name in ('dbc','target_fp')};kind='bp'
        id='upstream.diagnostic.'+str(self.version+1)
        payload={'object':kind,'checks':diagnostic,'read_only_parents':parents,
            'instruction':'Repair this object only from the supplied frozen originals. Preserve every real gap and conflict. Do not turn assertions, assumptions or missing evidence into guarantees.',
            'questions':['Resolve Constraint.evidence to the exact claim labels in this response; cite original support, not diagnostics.',
                'Recover all wire headers, register encodings, frame/data dependencies and supported success/failure/reentry paths required by the actual selected policy.',
                'Keep one normative physical relation with named helper quantities. Exact / is not C integer truncation; bit operations require declared width. Do not invent calibration validity or variable domains.',
                'For a sourced bounded bit relation declare PhysicalQuantity.bit_width on each named relation independently; it does not imply C truncation or arithmetic overflow semantics. Use pure requires/domain predicates or typed input bounds where justified, and keep genuinely unavailable domains/validity explicit.',
                'At the interpreted commit, old initialization frames are not current acquisition inputs. If the source persists calibration or validity, recover explicit typed FSM state and sourced Assign effects; field declarations alone do not create values. requires must use executable predicates over those actual bindings, not natural-language conditions or invented true flags.',
                'Lifecycle.variables may declare the same global persistent state without rewriting its control graph. Sequential Assign steps can copy successfully decoded inputs; use constructor_edits append to add one Sequence of such sourced assignments after a supported successful stage. Initialization and failure/invalidating paths must still establish or clear the declared validity; do not assume it.',
                'Retain small named intermediate relations to avoid unbalanced or opaque giant formulas. Missing mathematical syntax is a violation, while a solver resource limit or an unavailable valid input domain remains unknown.',
                'Separate unsupported/not applicable API channels and resources from genuinely unknown guarantees, using only values allowed by each field and actual source support.'],
            'scope':'method contract diagnostics only; no benchmark scoring, reference, hardware or calibration feedback'}
        if key=='target_fp':payload['questions']=[
            'Resolve Constraint.evidence to this response\'s exact claim labels and cite frozen framework originals.',
            'Recover only framework API signatures, acceptance/rejection, callback/resources, execution/lifecycle, clock conversion and output ABI semantics.',
            'A sourced synchronous sleep API may use SyncReturn.delay=ElapsedTimeLowerBound. Establish its successful-return condition, interruption failure behavior, native parameter units/range and maximum early loss from the original evidence; do not invent an alarm or assume a zero loss.',
            'Separate source-backed guarantees, provider candidates, inapplicable resources/channels and genuine missing guarantees. No device commands, frames, formulas or task policy belong in FP.']
        if key=='bp':payload['paths']=binding_inventory(parse(self.artifacts['dbc']['content'],'dbc'),parse(self.artifacts['target_fp']['content'],'fp'),self.workspace.task['target']['interface'])
        self.workspace.add_derived(id,id+'.json','internal_diagnostic','internal_diagnostic',canonical(payload)+'\n',
            [self.material_ids[key]],producer='method_contract_checker')
        selected += [{'role':'candidate','material_id':self.material_ids[key]},{'role':'internal_diagnostic','material_id':id}]
        self.update_evidence()
        extra={'repair_target':kind}
        if key=='dbc':extra['dbc_review']=True
        request=self.request('repair',selected,'仅修订指定的'+kind+'必要语义缺口。原始资料和父对象只读；诊断不是新事实，无法恢复的义务继续明确报告。',**extra)
        response,checks,_=self.runner.recover(request)
        if not self.accept(key,response,checks,selected,parents):return False
        self.checks.pop('generation_admission',None);self.checks.pop('recovery_support',None)
        self.saved_objects.pop(key,None)
        if key=='bp':self.check_saved_binding(self.workspace.task['target']['interface'])
        self.export_state()
        return checks['semantic_validation']['status']!='violated'

    def refine_prerequisites(self):
        attempted=self.upstream_repair_attempts;history=[]
        for _ in range(4):
            admission=self.assess_generation()
            history.append(admission)
            write_private_json(self.output/'upstream-admission-history.json',{'attempted_objects':attempted,'reports':history,
                'maximum_semantic_repairs':3,'maximum_per_object':1,'scope':'bounded prerequisite repairs; all actual requests also consume the shared request/time/cost limits'})
            if admission['status']=='satisfied':return True
            if len(attempted)>=3:return True
            roots=set()
            for finding in admission['blocking_findings']:
                key=finding['object'];subject=finding.get('subject','')
                if key=='binding':
                    key='dbc' if subject.startswith('dbc.') else 'target_fp' if subject.startswith('fp.') or finding['check']=='timing_provider_evidence' else 'bp'
                if key in {'dbc','target_fp','bp'}:roots.add(key)
            selected=next((key for key in ('dbc','target_fp','bp') if key in roots and key not in attempted
                and (key!='dbc' or self.policy.dbc_evidence_refinement) and (key!='bp' or self.policy.binding_plan)),None)
            if selected is None:return True  # Remains closed; propose_code records the exact stop.
            attempted.append(selected)
            write_private_json(self.output/'upstream-admission-history.json',{'attempted_objects':attempted,'reports':history,
                'maximum_semantic_repairs':3,'maximum_per_object':1,'scope':'bounded prerequisite repairs; all actual requests also consume the shared request/time/cost limits'})
            if not self.refine_contract(selected,admission):return False
            if self.policy.binding_plan and 'bp' not in self.revisions.current and not self.bp():return False
        return True

    def repair_candidate_prerequisite(self,diagnostics):
        """Route a typed checker gap, invalidate descendants and re-admit.

        A repaired parent never grants a new code budget. The caller must use
        repair_code with the old source as a seed and current read-only parents.
        """
        if not self.policy.candidate_contract_repair:
            raise ValueError('candidate contract feedback is disabled')
        revision=self.code_proposal['revision'] if self.code_proposal else None
        findings=[]
        for item in diagnostics:
            if (set(item)!={'schema','producer','candidate_revision','findings','scope'}
                    or item['schema']!='autodriver.upstream-candidate-diagnostic/v1'
                    or item['producer']!='method_trace_checker' or item['candidate_revision']!=revision
                    or not isinstance(item['findings'],list) or len(item['findings'])>128):
                raise ValueError('unapproved upstream candidate diagnostic')
            for finding in item['findings']:
                if (set(finding)!={'object','subject','status','check','detail'}
                        or finding['object'] not in {'dbc','target_fp','bp'} or finding['status']!='unknown'
                        or any(not isinstance(finding[k],str) for k in finding)):
                    raise ValueError('typed upstream checker finding required')
                findings.append(finding)
        roots={item['object'] for item in findings}
        key=next((key for key in ('dbc','target_fp','bp') if key in roots and key not in self.upstream_repair_attempts
            and (key!='dbc' or self.policy.dbc_evidence_refinement) and (key!='bp' or self.policy.binding_plan)),None)
        if key is None or len(self.upstream_repair_attempts)>=3:return False
        self.upstream_repair_attempts.append(key)
        write_private_json(self.output/'candidate-upstream-routing.json',{
            'candidate_revision':revision,'attempted_objects':self.upstream_repair_attempts,
            'selected_object':key,'findings':findings,'code_repairs_used':self.code_repairs_used(),
            'scope':'same three-object prerequisite repair bound across pre/post generation; no reset of code or host check limits'})
        if not self.refine_contract(key,{'candidate_revision':revision,'findings':[f for f in findings if f['object']==key]}):return False
        if self.policy.binding_plan and 'bp' not in self.revisions.current and not self.bp():return False
        return self.assess_generation()['status']=='satisfied'

    def propose_code(self,*,development_partial=False):
        admission=self.assess_generation()
        full_open=admission['status']=='satisfied'
        if admission['status']=='violated':return self.result('upstream_obligations_violated','generation_gate')
        if not full_open and not development_partial:return self.result('upstream_obligations_unresolved','generation_gate')
        dbc=parse(self.artifacts['dbc']['content'],'dbc')
        # A partial development proposal still needs concrete executable frame
        # and transaction facts; unknown protocol data is never made up here.
        for layout in dbc['frames'].values():check_layout(layout)
        scope=('all required upstream prerequisites checked; candidate implementation conformance remains to be checked after generation' if full_open else
            'development partial candidate only: implement the recovered concrete initialization/acquisition and data-integrity requirements, '
            'with explicit unresolved provider/lifecycle/notification premises. No full-conformance or formal-trial claim. '
            'Do not invent error parameters, recovery protocols, device constants or guarantees; unsupported paths remain gaps.')
        diagnostic={'classification':'generation_admission','full_scope_gate':'open' if full_open else 'closed',
            'development_partial_authorized':bool(development_partial),'checks':self.checks,'generation_scope':scope,
            'admission_location':'checks.generation_admission','candidate_conformance':'not_checked',
            'scope':'upstream checks only; no candidate has been compiled or executed'}
        parents=[self.material_ids[key] for key in ('dbc','target_fp')]+([self.material_ids['bp']] if self.policy.binding_plan else [])
        id='generation.admission.'+str(self.version+1)
        self.workspace.add_derived(id,id+'.json','internal_diagnostic','validation_report',
            canonical(diagnostic)+'\n',parents,producer='method_contract_checker')
        selected=self.workspace.selections({'method_specification','task_context','target_support'})
        selected += [{'role':'dbc','material_id':self.material_ids['dbc']},{'role':'target_fp','material_id':self.material_ids['target_fp']},
                     {'role':'validation_report','material_id':id}]
        # Code is driven by its complete current parents and public integration.
        # Framework originals remain registered/queryable; ancestry audit does
        # not require copying the entire FP extraction closure into this stage.
        if self.policy.binding_plan:selected.append({'role':'bp','material_id':self.material_ids['bp']})
        editable=['target/'+path for path in self.workspace.task['submission']['editable_paths']]
        from .protocol.assembler import path_allowed
        base={}
        for path,entry in self.workspace.manifest['files'].items():
            if not path.startswith('target/') or not path_allowed(path,{'editable_paths':editable}):continue
            if len(base)>=128:raise ValueError('candidate baseline file bound exceeded')
            code_id='code.baseline.'+str(len(base));self.workspace.add_original(code_id,path,'public_target_support','target_code',os_role='target')
            base[path]=entry['sha256'];selected.append({'role':'target_code','material_id':code_id})
        baseline_id='code.baseline.inventory'
        baseline={'schema':'autodriver.editable-baseline/v1','input_identity':self.workspace.manifest['identity'],
            'editable_paths':editable,'existing_files':base,
            'absent_explicit_files':[path for path in editable if not path.endswith('/') and path not in self.workspace.manifest['files']],
            'scope':'Complete prepared manifest inventory within editable paths. A listed absent file requires create, base_sha256=null. No existing same-device implementation is supplied.'}
        self.workspace.add_derived(baseline_id,'editable-baseline.json','public_target_support','target_support',
            canonical(baseline)+'\n',['task']+[s['material_id'] for s in selected if s['role']=='target_code'],os_role='target')
        selected.append({'role':'target_support','material_id':baseline_id})
        self.update_evidence()
        goal=('在主机明确给定范围内生成目标原生候选。只读父对象和公开ABI必须保持，未知保证不能写成已证明。'
            '完整驱动门禁是否开启在validation_report中单独披露；本次若为development partial，只交付可检查候选并完整报告限制。')
        extra={};stage='code_generate'
        if self.code_seed is not None:
            seed=self.code_seed
            self.runner.prior_code_repairs=seed['repair_depth']-1
            for key,expected in seed['parents'].items():
                if self.workspace.entries[self.material_ids[key]]['sha256']!=expected:
                    kind='fp' if key.endswith('_fp') else key
                    if seed.get('parent_semantics',{}).get(key)!=semantic_identity(self.workspace,self.artifacts[key]['content'],kind):
                        raise ValueError('code repair parent changed; upstream repair must be scheduled separately')
            if seed['base_files']!=base or seed['editable_paths']!=editable:raise ValueError('code repair baseline changed')
            seed_id='code.previous-candidate'
            seed_text=canonical(seed['artifact']) if seed['artifact'] is not None else seed['rejected_response_text']
            self.workspace.add_derived(seed_id,'previous-code.json','candidate_code','candidate',seed_text+'\n',parents,os_role='target')
            selected.append({'role':'candidate','material_id':seed_id})
            for number,item in enumerate(seed['diagnostics']):
                if item.get('producer') not in {'method_trace_checker','approved_compile_tool'}:
                    raise ValueError('only method internal/native and public compile diagnostics may repair code')
                id='code.internal-diagnostic.'+str(number)
                self.workspace.add_derived(id,id+'.json','internal_diagnostic','validation_report',canonical(item)+'\n',
                    [seed_id],producer=item['producer'])
                selected.append({'role':'internal_diagnostic','material_id':id})
            if seed['artifact'] is not None:
                self.revisions.add('code',seed['artifact'],{key:self.revisions.current[key] for key in seed['parents']},
                    provenance={'origin':seed['origin'],'upstream_revalidation':seed.get('upstream_revalidation')})
            stage='repair';extra={'repair_target':'code'}
            goal='仅修复当前 code 候选，依据公开构建和方法内部原生反例；FP、DBC、BP与公开ABI只读。响应必须给出针对最初prepared baseline的累计完整file_edits（原来不存在的文件仍用create），不要输出对旧candidate的局部diff。'+goal
            self.update_evidence()
        request=self.request(stage,selected,goal,
            editable_paths=editable,base_files=base,host_generation_gate='open',generation_scope=scope,
            generation_mode='with_binding' if self.policy.binding_plan else 'without_binding',**extra)
        response,checks,_=self.runner.recover(request)
        if response['artifact'] is None:return self.result('generation_no_candidate','code_generate')
        artifact=response['artifact'];revision=self.revisions.add('code',artifact,{key:self.revisions.current[key] for key in ('dbc','target_fp')+ (('bp',) if self.policy.binding_plan else ())},
            provenance={'call_id':response['call_id'],'generation_scope':scope,'full_scope_gate':diagnostic['full_scope_gate']})
        material='code.'+revision.identity[:16]
        self.workspace.add_derived(material,'code-'+revision.identity+'.json','candidate_code','candidate',canonical(artifact)+'\n',parents,os_role='target')
        self.artifacts['code']=artifact;self.material_ids['code']=material;self.checks['code']=checks['semantic_validation'];self.responses['code']=response
        self.code_proposal={'artifact':artifact,'request':self.runner.last_request,'revision':revision.identity,
            'generation_scope':scope,'full_scope_gate':diagnostic['full_scope_gate'],'applied':False}
        write_private_json(self.output/'code-proposal.json',self.code_proposal);self.export_state()
        return self.result('candidate_proposed' if full_open else 'development_partial_candidate_proposed','code_generate')

    def run(self,*,stop_after=None):
        try:return self._run(stop_after=stop_after)
        except (MethodLimitError,RejectedResponse,RejectedEnvelope) as error:
            # A bounded context/output failure is an algorithm terminal, not
            # permission for a fresh infrastructure retry or an older source.
            status='context_limit_exhausted' if isinstance(error,ContextLimitError) else 'method_limit_exhausted' if isinstance(error,MethodLimitError) else 'upstream_response_rejected'
            request=self.runner.last_request or {}
            if not isinstance(error,MethodLimitError) and (request.get('stage')=='code_generate' or request.get('repair_target')=='code'):
                status='generation_response_rejected'
            result=self.result(status,'bounded_recovery')
            result['failure']={'type':type(error).__name__,'reason':str(error)[:4096]}
            if isinstance(error,MethodLimitError):result['failure'].update(bound=error.kind,limit=error.limit,used=error.used)
            if isinstance(error,ContextLimitError):result['failure'].update(required_bytes=error.required_bytes,limit_bytes=error.limit_bytes)
            result['final_candidate_disposition']='no_submission_after_bounded_recovery'
            write_private_json(self.output/'pipeline-result.json',result)
            return result

    def _run(self,*,stop_after=None):
        self.select()
        if self.policy.source_framework_lifting and not self.fp('source'):return self.result('upstream_no_candidate','source_fp')
        if stop_after in {'fp','fp_refinement'}:
            if not self.fp('target'):return self.result('upstream_no_candidate','target_fp')
            if stop_after=='fp_refinement':
                ok=self.refine_contract('target_fp',self.checks['target_fp'])
                return self.result('development_stopped_after_fp_refinement' if ok else 'upstream_no_candidate','target_fp')
            return self.result('development_stopped_after_fp','fp')
        if not self.dbc():return self.result('upstream_no_candidate','dbc')
        if stop_after=='dbc':return self.result('development_stopped_after_dbc','dbc')
        if stop_after=='dbc_refinement':
            ok=self.refine_contract('dbc',self.checks['dbc'])
            return self.result('development_stopped_after_dbc_refinement' if ok else 'upstream_no_candidate','dbc')
        # Target FP is not a DBC parent. Recover it only when BP/direct
        # generation actually needs it; a failed DBC never triggers unrelated
        # framework calls. The complete path still checks the same parents.
        if not self.fp('target'):return self.result('upstream_no_candidate','target_fp')
        if self.policy.binding_plan and not self.bp():return self.result('upstream_no_candidate','bp')
        if stop_after=='bp':return self.result('development_stopped_after_bp','bp')
        if stop_after=='prototype':return self.propose_code(development_partial=True)
        if not self.refine_prerequisites():return self.result('upstream_no_candidate','upstream_repair')
        # Unknown upstream prerequisites keep generation closed. Candidate
        # conformance belongs after generation and cannot be a prerequisite.
        return self.propose_code()

    def code_repairs_used(self):
        prior=self.code_seed['repair_depth']-1 if self.code_seed is not None else 0
        return prior+sum(call.get('stage')=='repair' and call.get('repair_target')=='code' for call in self.runner.calls)

    def repair_code(self,diagnostics,*,prior_proposal=None):
        """One bounded code-only revision; upstream objects remain read-only."""
        used=self.code_repairs_used()
        if used>=4:raise ValueError('cumulative four code repairs exhausted')
        if not self.code_proposal and prior_proposal is None:raise ValueError('code repair needs a current proposed candidate')
        allowed={'approved_compile_tool'}|({'method_trace_checker'} if self.policy.candidate_contract_repair else set())
        if not diagnostics or any(item.get('producer') not in allowed for item in diagnostics):
            raise ValueError('unapproved candidate repair diagnostic')
        old=self.code_proposal if prior_proposal is None else prior_proposal
        previous=old['request'];number=self.version+1
        parents=[self.material_ids[key] for key in ('dbc','target_fp')]+([self.material_ids['bp']] if self.policy.binding_plan else [])
        seed='code.repair-seed.'+str(number)
        self.workspace.add_derived(seed,seed+'.json','candidate_code','candidate',canonical(old['artifact'])+'\n',
            list(dict.fromkeys(['code.'+old['revision'][:16]]+parents)),os_role='target')
        excluded={'candidate','internal_diagnostic'}
        if prior_proposal is not None:excluded|={'dbc','target_fp','bp','validation_report'}
        selected=[item for item in previous['selections'] if item['role'] not in excluded]
        if prior_proposal is not None:
            admission=self.assess_generation()
            if admission['status']!='satisfied':raise ValueError('changed parents require current generation admission')
            selected += [{'role':key,'material_id':self.material_ids[key]} for key in ('dbc','target_fp')+(('bp',) if self.policy.binding_plan else ())]
            gate='generation.admission.'+str(number)
            self.workspace.add_derived(gate,gate+'.json','internal_diagnostic','validation_report',canonical(admission)+'\n',parents,
                producer='method_contract_checker')
            selected.append({'role':'validation_report','material_id':gate})
        selected.append({'role':'candidate','material_id':seed})
        for i,item in enumerate(diagnostics):
            id='code.repair-diagnostic.'+str(number)+'.'+str(i)
            self.workspace.add_derived(id,id+'.json','internal_diagnostic','internal_diagnostic',canonical(item)+'\n',
                [seed],os_role='target',producer=item['producer'])
            selected.append({'role':'internal_diagnostic','material_id':id})
        self.update_evidence()
        # This first repair consumes one slot; every format repair also counts.
        # CallRunner checks the cumulative code counter before every request,
        # including format/evidence continuations. Do not shrink other objects'
        # independent format-repair bound after a code repair.
        request=self.request('repair',selected,
            '仅修复当前code候选，依据当前公开编译或方法内部原生反例。FP/DBC/BP与ABI只读。'
            '针对最初prepared baseline输出累计完整file_edits；原来不存在的文件仍用create。不得降低约束、改设备策略或使用旧样本伪装成功。',
            repair_target='code',editable_paths=previous['editable_paths'],base_files=previous['base_files'],
            host_generation_gate='open',generation_scope=old['generation_scope'],
            generation_mode=previous['generation_mode'])
        response,checks,_=self.runner.recover(request)
        if response['artifact'] is None:return self.result('repair_no_candidate','code_repair')
        artifact=response['artifact'];revision=self.revisions.add('code',artifact,
            {key:self.revisions.current[key] for key in ('dbc','target_fp')+(('bp',) if self.policy.binding_plan else ())},
            provenance={'call_id':response['call_id'],'repair_index':used+1,'full_scope_gate':old['full_scope_gate']})
        material='code.'+revision.identity[:16]
        self.workspace.add_derived(material,'code-'+revision.identity+'.json','candidate_code','candidate',canonical(artifact)+'\n',parents,os_role='target')
        self.artifacts['code']=artifact;self.material_ids['code']=material;self.checks['code']=checks['semantic_validation'];self.responses['code']=response
        self.code_proposal={'artifact':artifact,'request':self.runner.last_request,'revision':revision.identity,
            'generation_scope':old['generation_scope'],'full_scope_gate':old['full_scope_gate'],'applied':False}
        write_private_json(self.output/'code-proposal.json',self.code_proposal);self.export_state()
        return self.result('candidate_proposed','code_repair')

    def result(self,status,stage):
        result={'status':status,'stage':stage,'logical_calls':len(self.runner.calls),
                'local_saved_responses':sum(c['execution_mode']=='saved_response_local_validation' for c in self.runner.calls),'candidate':None,
                'checks':self.checks,'revision_ids':self.revisions.current,'hardware_status':'not_run',
                'code_proposal':self.code_proposal,
                'scope':'development contract pipeline; generation/repair gate remains closed when required obligations are unresolved'}
        write_private_json(self.output/'pipeline-result.json',result)
        return result
