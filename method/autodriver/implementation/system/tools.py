"""Real, bounded method tools; no benchmark evaluation dependency."""
from copy import deepcopy
from pathlib import Path
import time

import jsonschema

from .evidence import SourceIndex,extract_pdf,clang_facts
from .protocol.assembler import query_scopes,available_tools,expected_kind
from .contract.check import check_contract
from .contract.model import ContractError,Unknown


def obj(properties,required):
    return {'type':'object','properties':properties,'required':required,'additionalProperties':False}


TEXT={'type':'string','minLength':1,'maxLength':512}
SCOPE={'enum':['source','device_documents','source_framework','target_framework','target_support']}
SPECS={
    'inspect_symbol':obj({'scope':SCOPE,'symbol':{'anyOf':[TEXT,
        {'type':'array','minItems':1,'maxItems':16,'items':TEXT}],
        'description':'One exact indexed symbol, up to16 whitespace-separated exact selectors, or an array of up to16 exact selectors. Unknown names stay unresolved; namespaces and spellings are not guessed.'}},['scope','symbol']),
    'query_framework':obj({'scope':{'enum':['source_framework','target_framework']},'query':TEXT},['scope','query']),
    'slice_device_effects':obj({'roots':{'type':'array','minItems':1,'maxItems':32,'items':TEXT}},['roots']),
    'resolve_configuration':obj({'material_id':TEXT},['material_id']),
    'retrieve_device_evidence':obj({'query':TEXT},['query']),
    'read_evidence_range':obj({'scope':SCOPE,'material_id':TEXT,
        'start_line':{'type':'integer','minimum':1,'maximum':100000000},
        'end_line':{'type':'integer','minimum':1,'maximum':100000000}},['scope','material_id','start_line','end_line']),
    'check_contract':obj({'content':{'type':'string','minLength':1,'maxLength':2000000},'kind':{'enum':['fp','dbc','bp']}},['content','kind']),
}


class ToolExecutor:
    def __init__(self,evidence,request,*,configuration=None):
        self.evidence,self.request=evidence,dict(request)
        self.configuration=None if configuration is None else dict(configuration)
        self.events=[];self.scope_aliases={}

    def specs(self):
        # Only tools with actual executors are advertised. Public compilation
        # and native candidate execution are intentionally absent at this stage.
        specifications={name:deepcopy(SPECS[name]) for name in available_tools(self.request) if name in SPECS
                and not (name=='check_contract' and expected_kind(self.request)=='code')}
        if 'read_evidence_range' in specifications:
            specifications['read_evidence_range']['properties']['scope']['enum']=sorted(query_scopes(self.request)&set(SCOPE['enum']))
        return specifications

    def scope_ids(self,scope,*,deduplicate=True):
        if scope not in query_scopes(self.request):raise ContractError('tool scope is forbidden in this call')
        allowed={
            'source':{'device_source'},'device_documents':{'device_document','document_extract'},
            'source_framework':{'framework_source','framework_document','fp_shared','fp_task_local'},
            'target_framework':{'framework_source','framework_document','fp_shared','fp_task_local'},
            'target_support':{'public_target_support'},
        }.get(scope,set())
        os_role='source' if scope=='source_framework' else 'target' if scope=='target_framework' else None
        ids=[id for id,e in self.evidence.registry.items() if e['kind'] in allowed and (os_role is None or e['os_role']==os_role)]
        if not deduplicate:return ids
        selected=[];seen={};aliases=[]
        for id in ids:
            entry=self.evidence.registry[id];kind=entry['kind'];key=None
            if kind in {'device_source','device_document','framework_source','framework_document','public_target_support'}:
                # Same prepared file, bytes and lineage; a content hash alone
                # must never merge distinct translation units or resources.
                key=(kind,entry['path'],entry['sha256'],entry['os_role'],
                     tuple(sorted((p,self.evidence.registry[p]['sha256']) for p in entry['parents'])))
            elif kind=='document_extract' and len(entry['parents'])==1:
                parent=self.evidence.registry[entry['parents'][0]]
                if parent['kind']=='device_document':
                    key=(kind,entry['sha256'],entry['os_role'],parent['path'],parent['sha256'])
            if key is not None and key in seen:
                aliases.append({'material_id':id,'canonical_material_id':seen[key],
                                'sha256':entry['sha256'],'reason':'same frozen input file or same PDF/extracted bytes'})
            else:
                selected.append(id)
                if key is not None:seen[key]=id
        self.scope_aliases[scope]=aliases
        return selected

    def index(self,scope):
        index=SourceIndex(self.evidence)
        for material_id in self.scope_ids(scope):
            e=self.evidence.registry[material_id]
            if e['kind'] in {'device_source','framework_source','public_target_support'} and Path(e['path']).suffix in {'.c','.h','.rs'}:
                index.add(material_id)
        return index

    def execute(self,name,arguments):
        if name not in self.specs():raise ContractError('tool is not implemented or allowed in this stage')
        try: jsonschema.Draft202012Validator(self.specs()[name]).validate(arguments)
        except jsonschema.ValidationError:raise ContractError('tool arguments exceed finite schema') from None
        started=time.monotonic()
        try:
            if name=='read_evidence_range':
                material=arguments['material_id'];scope=arguments['scope']
                if material not in self.scope_ids(scope,deduplicate=False):raise ContractError('material is outside the approved call scope')
                if self.evidence.registry[material]['kind'] not in {'device_source','device_document','document_extract','framework_source','framework_document','public_target_support'}:
                    raise ContractError('range reads require original text or mechanical document extraction, not contracts, indices or diagnostics')
                excerpt=self.evidence.excerpt(material,arguments['start_line'],arguments['end_line'],maximum_lines=6000)
                result={'excerpts':[excerpt],'complete':False,
                    'scope':'exact requested original lines only; semantic dependencies, cross-page footnotes and unencoded properties need further recovery'}
            elif name=='inspect_symbol':
                supplied=arguments['symbol'];roots=supplied.split() if isinstance(supplied,str) else list(supplied)
                if not 1<=len(roots)<=16:raise ContractError('inspect_symbol requires1..16 exact symbol selectors; use smaller complete questions')
                index=self.index(arguments['scope']);result=index.excerpts(roots,maximum_lines=6000)
                result['excerpts']=[self.evidence.excerpt(s['material_id'],s['start_line'],s['end_line'],maximum_lines=6000) for s in result['selections']]
                names=sorted(index.by_name)
                result.update(indexed_material_ids=sorted(index.materials),index_gaps=index.gaps,
                    indexed_name_count=len(names),exact_indexed_names=names if len(names)<=128 else None,
                    index_scope='parsed locations and bounded known type/call closure only; effective configuration, dynamic/provider dispatch and semantic guarantees require separate evidence')
            elif name=='slice_device_effects':
                from .source_analysis import CAnalysis,cmake_source_membership
                analysis=CAnalysis(self.evidence,self.configuration);membership=[]
                for id in self.scope_ids('source'):
                    entry=self.evidence.registry[id]
                    if Path(entry['path']).name=='CMakeLists.txt':
                        membership+=cmake_source_membership(self.evidence.read(id).decode(),str(Path(entry['path']).parent),self.configuration or {})
                for id in self.scope_ids('source'):
                    entry=self.evidence.registry[id]
                    if Path(entry['path']).suffix not in {'.c','.h'}:continue
                    records=[row for row in membership if row['path']==entry['path']]
                    if records and all(row['active'] is False for row in records):
                        analysis.excluded.append({'material_id':id,'sha256':entry['sha256'],'build_membership':records,'reason':'translation unit excluded by frozen configuration'})
                    else:analysis.add(id)
                result=analysis.slice(arguments['roots'])
                result['translation_unit_external_calls']=sorted({call['callee'] for call in analysis.report()['calls'] if not call['targets']})
                found={row['name'] for row in result['functions'].values()}|set(result['functions'])
                missing=set(arguments['roots'])-found
                if missing:raise Unknown('source function roots not resolved: '+', '.join(sorted(missing)))
                result['semantic_effects']='unknown: complete selected function/call control-data closure is mechanical may-analysis, not device semantics'
            elif name=='resolve_configuration':
                if arguments['material_id'] not in self.scope_ids('source',deduplicate=False):raise ContractError('configuration query must use this task source')
                if self.configuration is None:raise Unknown('no frozen macro configuration supplied; do not analyze default branches as selected branches')
                result=clang_facts(self.evidence,arguments['material_id'],self.configuration)
                result['configuration_completeness']='explicit frozen macro selections only; remaining Kconfig/devicetree/include dependencies are not proven complete'
            elif name in {'query_framework','retrieve_device_evidence'}:
                scope=arguments['scope'] if name=='query_framework' else 'device_documents'
                material_ids=self.scope_ids(scope)
                text_ids=[id for id in material_ids if not self.evidence.registry[id]['path'].lower().endswith('.pdf')]
                # Query strings are literal terms, never executable commands.
                result=self.evidence.search(text_ids,arguments['query'].split()[:16])
                if name=='query_framework':
                    index=self.index(scope)
                    roots=[word for word in arguments['query'].split()[:16] if word in index.by_name]
                    if roots:
                        closure=index.excerpts(roots,maximum_lines=6000)
                        result=dict(closure,excerpts=[self.evidence.excerpt(s['material_id'],s['start_line'],s['end_line'],maximum_lines=6000)
                                                     for s in closure['selections']])
                if name=='retrieve_device_evidence':
                    result['original_pdf_ids']=[id for id in material_ids if self.evidence.registry[id]['path'].lower().endswith('.pdf')]
            else:
                if arguments['kind']!=expected_kind(self.request):raise ContractError('tool cannot check a different repair object')
                _,result=check_contract(arguments['content'],arguments['kind'])
            event={'tool':name,'arguments':arguments,'seconds':time.monotonic()-started,
                                'status':'returned','scope_aliases':dict(self.scope_aliases),
                                'source_manifest':{id:e['sha256'] for id,e in self.evidence.registry.items()}}
            if name=='inspect_symbol':event['index_result']={key:value for key,value in result.items() if key!='excerpts'}
            self.events.append(event)
            return result
        except BaseException as error:
            self.events.append({'tool':name,'seconds':time.monotonic()-started,'status':'error','error_type':type(error).__name__})
            raise
