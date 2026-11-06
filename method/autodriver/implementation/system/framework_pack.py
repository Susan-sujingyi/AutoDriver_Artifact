"""Task-independent, pinned framework-only development knowledge versions.

A frozen candidate records unresolved semantics. It is never a proof, a device
contract cache or an automatic replacement for a published formal FP version.
"""
from copy import deepcopy

from .artifacts import import_bundle
from .contract.model import parse,ContractError
from .protocol.assembler import canonical,digest


KINDS={'framework_scope','framework_source','framework_document','method_specification','fp_shared','fp_task_local'}


def validate(pack,*,require_frozen=False):
    if pack.get('schema')!='autodriver.framework-pack/v1':raise ContractError('unsupported framework pack')
    status=pack.get('release_status')
    if status not in {'development_candidate','frozen'} or require_frozen and status!='frozen':
        raise ContractError('formal FP admission requires its own frozen release')
    contents={key:value for key,value in pack.items() if key!='version'}
    if pack.get('version')!=digest(canonical(contents).encode()):raise ContractError('framework pack version changed')
    if set(pack)-{'schema','scope','object','materials','release_status','version','origin','publication'}:raise ContractError('framework pack contains non-framework metadata')
    if status=='frozen':
        publication=pack.get('publication',{})
        if (set(publication)!={'schema','request_ids','validation_sha256','construction_sha256','assurance'}
                or publication.get('schema')!='autodriver.framework-publication/v1'
                or publication.get('assurance')!='frozen provenance and explicit gaps; no universal semantic proof'):
            raise ContractError('frozen FP publication metadata required')
        ids=publication['request_ids']
        if (not isinstance(ids,list) or not 1<=len(ids)<=512 or any(not isinstance(value,str) or not value for value in ids)
                or len(ids)!=len(set(ids)) or pack['origin']['request_id'] not in ids):
            raise ContractError('original FP construction requests required')
        for field in ('validation_sha256','construction_sha256'):
            value=publication[field]
            if not isinstance(value,str) or len(value)!=64 or set(value)-set('0123456789abcdef'):
                raise ContractError('frozen FP evidence identity required')
    elif 'publication' in pack:raise ContractError('development candidate cannot claim a publication')
    entries={entry['id']:entry for entry in pack['materials']}
    if not entries or len(entries)!=len(pack['materials']) or len(entries)>128:raise ContractError('bounded unique FP closure required')
    for entry in entries.values():
        if entry.get('kind') not in KINDS:raise ContractError('shared FP contains device/task/diagnostic material')
        if set(entry.get('parents',[]))-entries.keys():raise ContractError('shared FP closure is incomplete')
    root=entries[pack['object']['material_id']]
    if root['kind'] not in {'fp_shared','fp_task_local'}:raise ContractError('shared framework root must be FP')
    fp=parse(root['text'],'fp');scope=pack['scope']
    if (scope['os_role'] not in {'source','target'} or fp['os']!=scope['os'] or fp['snapshot'].name!='Ref'
            or entries[fp['snapshot']['identifier']]['sha256']!=scope['scope_sha256']):raise ContractError('shared framework scope mismatch')
    if pack['object'].get('parents') or pack['object'].get('reviewed_task_sha256') is not None:
        raise ContractError('shared FP must have no task/device parent')
    return fp


def import_pack(workspace,pack,role,current_scope):
    validate(pack);scope=pack['scope'];os_name=workspace.task['source' if role=='source' else 'target']['os']
    if scope['os']!=os_name or scope['os_role']!=role:
        raise ContractError('shared FP is for a different OS/role')
    key=role+'_fp'
    bundle={'schema':'autodriver.contract-bundle/v1','task_id':workspace.task['task_id'],'condition':'SD',
        'input_identity':workspace.manifest['identity'],'objects':{key:deepcopy(pack['object'])},'materials':deepcopy(pack['materials'])}
    objects,audit=import_bundle(workspace,bundle)
    objects[key]['framework_version']=pack['version'];audit['framework_version']=pack['version']
    audit['scope_current']=scope['scope_sha256']==workspace.entries[current_scope]['sha256']
    # A larger current declaration/capability scope triggers a task-local FP
    # build with the old pack only as a draft. All original pack bytes must
    # still match the current prepared originals; input drift is refused.
    return objects[key],audit
