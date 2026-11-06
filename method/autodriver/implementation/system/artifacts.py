"""Import receipt-attested contract data, never old prompts or request logs.

Original materials are verified against the current prepared manifest. Imported
derived values receive a separate namespace, so a changed task projection or
framework scope cannot silently replace the object a saved Ref denoted.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import PurePosixPath
import yaml

from .protocol.assembler import canonical,digest,virtual_parts,PROTOCOLS
from .contract.model import parse,ContractError,Pinned,References,Node,identity
from .contract.check import check_contract,include_response_gaps


CONTRACT_KINDS={'fp_shared':'fp','fp_task_local':'fp','dbc_candidate':'dbc','bp_candidate':'bp'}
OBJECT_KINDS={'source_fp':'fp','target_fp':'fp','dbc':'dbc','bp':'bp'}


def semantic_identity(workspace,text,kind):
    """Ignore audited material alias renames, preserving their actual bytes.

    This is identity comparison, never evidence entailment or check promotion.
    """
    return semantic_identity_catalog(text,kind,workspace.entries,lambda id:workspace.evidence().read(id))


def semantic_identity_catalog(text,kind,entries,read):
    active=set()
    def convert(value):
        if isinstance(value,Node):
            if value.name=='Ref' and value['identifier'] in entries:
                id=value['identifier'];entry=entries[id]
                if id in active:raise ContractError('cyclic semantic material reference')
                childkind=CONTRACT_KINDS.get(entry['kind'])
                if childkind:
                    active.add(id)
                    result=convert(parse(read(id).decode(),childkind));active.remove(id)
                    return {'material_contract':result}
                return {'material_sha256':entry['sha256'],'kind':entry['kind'],'os_role':entry['os_role']}
            return {'constructor':value.name,'fields':{key:convert(item) for key,item in value.fields.items()}}
        if hasattr(value,'items'):return {str(key):convert(item) for key,item in value.items()}
        if isinstance(value,(tuple,list)):return [convert(item) for item in value]
        return value
    return identity(convert(parse(text,kind)))


def rebase_constructor(text,kind,names):
    original=parse(text,kind)
    changes=[]
    class Rebase(ast.NodeTransformer):
        def visit_Call(self,node):
            self.generic_visit(node)
            if isinstance(node.func,ast.Name) and node.func.id=='Ref':
                argument=node.args[0] if node.args else next(k.value for k in node.keywords if k.arg=='identifier')
                if isinstance(argument,ast.Constant) and isinstance(argument.value,str):
                    old=argument.value
                    if old in names:
                        argument.value=names[old];changes.append({'from':old,'to':names[old]})
            return node
    tree=Rebase().visit(ast.parse(text,mode='eval'));ast.fix_missing_locations(tree)
    output=ast.unparse(tree.body)
    parse(output,kind)
    return output,changes


def references_for(workspace):
    objects={};expected={}
    for id,entry in workspace.entries.items():
        data=workspace.evidence().read(id)
        try:
            text=data.decode('utf8')
            if entry['kind'] in CONTRACT_KINDS:value=parse(text,CONTRACT_KINDS[entry['kind']])
            elif entry['kind']=='source_analysis':
                value=yaml.safe_load(text)
            elif entry['kind'] in {'framework_scope','public_task_projection'} or text.lstrip().startswith(('{','[')):
                try:value=json.loads(text)
                except ValueError:value=text
            elif entry['kind']=='source_configuration' and entry['path'].endswith(('.yaml','.yml')):
                value=yaml.safe_load(text)
            else:value=text
        except (UnicodeDecodeError,ContractError,ValueError,yaml.YAMLError):value=None
        objects[id]=Pinned(value,entry['sha256']);expected[id]=entry['sha256']
    return References(objects,expected)


def import_bundle(workspace,bundle):
    if bundle.get('schema')!='autodriver.contract-bundle/v1':raise ContractError('unsupported contract bundle')
    if (bundle.get('task_id')!=workspace.task['task_id'] or bundle.get('condition')!='SD'
            or bundle.get('input_identity')!=workspace.manifest['identity']):
        raise ContractError('contract bundle belongs to different prepared inputs')
    materials=bundle.get('materials',[])
    if not materials or len(materials)>512:raise ContractError('bounded contract material closure required')
    records={entry['id']:entry for entry in materials}
    if len(records)!=len(materials):raise ContractError('duplicate bundle material ID')
    allowed=set().union(*map(set,PROTOCOLS['role_kinds'].values()))
    for entry in materials:
        if entry.get('kind') not in allowed or entry.get('approved') is not True:
            raise ContractError('forbidden or unapproved imported material')
        if entry['kind']=='internal_diagnostic' and entry.get('producer') not in PROTOCOLS['diagnostic_producers']:
            raise ContractError('unapproved imported diagnostic')
        if set(entry.get('parents',[]))-records.keys():raise ContractError('bundle source closure is incomplete')
        if entry['kind'] in PROTOCOLS['derived_kinds'] and not entry.get('parents'):
            raise ContractError('derived imported value has no source closure')
        if 'original_path' in entry:
            path=entry['original_path'];selected={item['path'] for item in workspace.task['source']['selected_files']}
            expected=('device_source' if path in selected else 'source_configuration' if path in workspace.task['source']['configuration_files']
                else 'public_target_support' if path in workspace.task['target']['interface_files'] else 'device_document' if path.startswith('documents/') else None)
            if expected and entry['kind']!=expected:raise ContractError('original material has been relabeled')
            if path.startswith('documents/') and not path.lower().endswith('.pdf'):raise ContractError('only approved raw PDF documents are admitted')
            if entry['kind'] in {'framework_source','framework_document'}:
                from .framework import FRAMEWORK_PREFIXES,ROOTS,cargo_dependencies
                source=path.startswith('source_os/');base='source_os/' if source else 'target/'
                os_name=workspace.task['source' if source else 'target']['os'];local=path.removeprefix(base)
                generic=(local.startswith(FRAMEWORK_PREFIXES[os_name]) or os_name=='tock' and local.startswith('libraries/')
                         or os_name=='tock' and local in {name for group in ROOTS['tock'].values() for name in group}
                         or os_name=='tock' and entry['kind']=='framework_document' and local.startswith(('boards/','capsules/')) and local.endswith('/Cargo.toml')
                         or os_name=='nuttx' and local=='drivers/sensors/sensor.c')
                if os_name=='tock' and not source and local.startswith('boards/') and local.endswith('/src/main.rs'):
                    cargo=cargo_dependencies(workspace,base,workspace.task['target'].get('board',''))
                    mains={str(PurePosixPath(path).parent/'src/main.rs') for path in cargo['manifests']
                           if PurePosixPath(path).parent.name==workspace.task['target'].get('board')}
                    generic=path in mains
                if not path.startswith(base) or not generic:raise ContractError('imported framework material is outside the generic scope')
            original=workspace.read_original(entry['original_path'])
            if digest(original)!=entry['sha256']:raise ContractError('original bundle source changed')
        elif not isinstance(entry.get('text'),str) or digest(entry['text'].encode())!=entry['sha256']:
            raise ContractError('derived bundle material changed')
    fp_allowed={'framework_scope','framework_source','framework_document','method_specification','fp_shared','fp_task_local','internal_diagnostic'}
    for entry in materials:
        if entry['kind'] not in {'fp_shared','fp_task_local'}:continue
        pending=list(entry.get('parents',[]));seen=set()
        while pending:
            id=pending.pop()
            if id in seen:continue
            seen.add(id);parent=records[id]
            if parent['kind'] not in fp_allowed:raise ContractError('FP source closure contains device/task material')
            pending.extend(parent.get('parents',[]))
    bundle_sha=digest(canonical(bundle).encode());prefix='saved.'+bundle_sha[:12]+'.';names={}
    for id,entry in records.items():
        same=next((current for current,value in workspace.entries.items()
            if 'original_path' in entry and value['path']==entry['original_path'] and value['sha256']==entry['sha256'] and value['kind']==entry['kind']),None)
        names[id]=same or prefix+id
    pending=set(records);edits=[]
    while pending:
        ready=sorted(id for id in pending if not set(records[id].get('parents',[]))&pending)
        if not ready:raise ContractError('cyclic imported source closure')
        for id in ready:
            record=records[id];new=names[id];parents=[names[p] for p in record.get('parents',[])]
            if new not in workspace.entries:
                if 'original_path' in record:
                    workspace.add_original(new,record['original_path'],record['kind'],'ancestor',os_role=record['os_role'])
                else:
                    text=record['text']
                    if record['kind'] in CONTRACT_KINDS:
                        try:
                            text,changed=rebase_constructor(text,CONTRACT_KINDS[record['kind']],names)
                            text+='\n';edits.extend({'material_id':id,**change} for change in changed)
                        except ContractError:
                            # Invalid historical repair ancestors are opaque data,
                            # never current executable contract objects.
                            pass
                    workspace.add_derived(new,'imports/'+bundle_sha[:12]+'/'+digest(id.encode())+'.txt',
                        record['kind'],'ancestor',text,parents,os_role=record['os_role'],producer=record.get('producer'))
            pending.remove(id)
    references=references_for(workspace);objects={}
    for key,record in bundle.get('objects',{}).items():
        if key not in OBJECT_KINDS or record['material_id'] not in names:raise ContractError('unknown imported contract object')
        id=names[record['material_id']];entry=workspace.entries[id];kind=OBJECT_KINDS[key]
        if CONTRACT_KINDS.get(entry['kind'])!=kind:raise ContractError('imported object kind changed')
        text=workspace.evidence().read(id).decode().rstrip('\n')
        links=[]
        for link in record.get('evidence_links',[]):
            link=deepcopy(link);link['material_id']=names[link['material_id']]
            total=len(workspace.evidence().read(link['material_id']).decode().splitlines())
            if records[next(old for old,new in names.items() if new==link['material_id'])]['kind'] in CONTRACT_KINDS:
                link['start_line'],link['end_line']=1,total
            if not 1<=link['start_line']<=link['end_line']<=total:raise ContractError('imported evidence range changed')
            links.append(link)
        root,checks=check_contract(text,kind,references=references,evidence_claims=[link['claim'] for link in links])
        checks=include_response_gaps(checks,record.get('unresolved',[]))
        objects[key]={'artifact':{'kind':kind,'format':'constructor','content':text},'material_id':id,
            'checks':checks,'evidence_links':links,'unresolved':record.get('unresolved',[]),
            'evidence_refined':bool(record.get('evidence_refined',False)),
            'seed_only':record.get('seed_only',False),'source_response_protocol_error':record.get('source_response_protocol_error'),
            'origin':record['origin'],'reviewed_task_sha256':record.get('reviewed_task_sha256'),
            'parents':record.get('parents',{}),'parent_materials':{key:names[value] for key,value in record.get('parents',{}).items()},
            'bundle_sha256':bundle_sha,'root':root}
    return objects,{'schema':'autodriver.contract-import/v1','bundle_sha256':bundle_sha,'names':names,
                    'reference_rebindings':edits,'new_upstream_requests':0,
                    'scope':'receipt-attested saved candidates rechecked under current kernel; no old assurance is promoted to pass'}
