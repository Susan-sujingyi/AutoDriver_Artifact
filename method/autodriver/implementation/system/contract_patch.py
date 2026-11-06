"""Model-authored field edits on one exact, visible contract candidate.

Only the transport changes. Unedited nodes and literal values are retained;
every resulting object still passes the ordinary parser and semantic checks.
"""
import ast
from copy import deepcopy
import json

from .contract.model import parse,ContractError
from .protocol.language_catalog import load_catalog
from .protocol.assembler import canonical,digest
from .transport import render_tree


def tree_from_text(text,kind):
    original=parse(text,kind);catalog=load_catalog()
    def convert(value):
        if isinstance(value,ast.Call):
            entry=catalog[value.func.id];result={'$constructor':value.func.id}
            for name,item in zip(entry['positional'],value.args):result[name]=convert(item)
            if len(value.args)>len(entry['positional']):result[entry['varargs']]=[convert(item) for item in value.args[len(entry['positional']):]]
            result.update({item.arg:convert(item.value) for item in value.keywords});return result
        if isinstance(value,ast.Constant):
            return {'$decimal':ast.get_source_segment(text,value)} if type(value.value) is float else value.value
        if isinstance(value,ast.UnaryOp):
            inner=convert(value.operand)
            if isinstance(value.op,ast.UAdd):return inner
            if type(inner) is int:return -inner
            literal=inner['$decimal']
            return {'$decimal':literal[1:] if literal.startswith('-') else '-'+literal.lstrip('+')}
        if isinstance(value,ast.List):return [convert(item) for item in value.elts]
        if isinstance(value,ast.Tuple):return {'$tuple':[convert(item) for item in value.elts]}
        if isinstance(value,ast.Dict):
            pairs=[(convert(key),convert(item)) for key,item in zip(value.keys,value.values)]
            if all(isinstance(key,str) and not key.startswith('$') for key,_ in pairs):return dict(pairs)
            return {'$map':[[key,item] for key,item in pairs]}
        raise ContractError('unsupported contract carrier node')
    tree=convert(ast.parse(text,mode='eval').body)
    if parse(render_tree(tree,kind),kind)!=original:raise ContractError('contract base carrier changed a value')
    return tree


def repair_base(request,kind,selections):
    if request['stage']!='repair' or kind=='code':return None
    candidates=[item for item in selections if item['role']=='candidate']
    if len(candidates)!=1:return None
    item=candidates[0]
    if item['start_line']!=1 or item['end_line']!=item['total_lines']:return None
    try:tree_from_text(item['text'],kind)
    except (ValueError,TypeError,KeyError,RecursionError):return None
    return {'kind':kind,'material_id':item['material_id'],'sha256':item['sha256'],
        'path_form':'named constructor fields / map keys / zero-based list indices; reserved $ carrier keys are immutable',
        'operations':['replace','add','append'],'maximum_edits':32,
        'scope':'this exact visible candidate only; read-only parents and all ordinary semantic checks remain in force'}


def apply_response(response,messages):
    if messages is None:raise ContractError('contract edits require their exact current assembled call')
    data=json.loads(messages[-1]['content']);base=data.get('contract_repair_base');artifact=response['artifact']
    from .protocol.schemas import CONTRACT_EDITS
    from .protocol.assembler import schema_check
    try:schema_check(artifact,CONTRACT_EDITS)
    except ValueError as error:raise ContractError(str(error)) from None
    if not isinstance(base,dict) or response.get('stage')!='repair':raise ContractError('contract edits are unavailable in this call')
    if artifact['kind']!=base['kind'] or artifact['base_material_id']!=base['material_id'] or artifact['base_sha256']!=base['sha256']:
        raise ContractError('contract edits do not match the current exact base identity; use these current template values: '+canonical({
            'kind':base['kind'],'base_material_id':base['material_id'],'base_sha256':base['sha256']}))
    matches=[item for item in data['context'] if item['role']=='candidate' and item['material_id']==base['material_id']]
    if len(matches)!=1:raise ContractError('contract edit base is absent or ambiguous')
    context=matches[0];text=context['text']
    if (context['start_line']!=1 or context['end_line']!=context['total_lines']
            or digest(text.encode())!=base['sha256'] or context['sha256']!=base['sha256']):raise ContractError('contract edit base bytes or extent changed')
    original=tree_from_text(text,base['kind']);tree=deepcopy(original);edits=artifact['edits'];paths=[];audit=[];appended={}
    if not isinstance(edits,list) or not 1<=len(edits)<=32:raise ContractError('contract edits require 1..32 explicit operations')
    for edit in edits:
        if set(edit)!={'operation','path','value'} or edit['operation'] not in {'replace','add','append'}:raise ContractError('contract edits support only explicit replace/add/append')
        path=edit['path']
        if not isinstance(path,list) or not 1<=len(path)<=32 or any(type(part) not in {str,int} or isinstance(part,str) and (not part or part.startswith('$')) or type(part) is int and part<0 for part in path):
            raise ContractError('invalid contract field path')
        parent=original
        if edit['operation']=='append':
            for part in path:
                if isinstance(parent,list) and type(part) is int and part<len(parent) or isinstance(parent,dict) and isinstance(part,str) and part in parent:parent=parent[part]
                else:raise ContractError('append target does not resolve in the original contract')
            if not isinstance(parent,list):raise ContractError('append requires an existing list')
            key=tuple(path);offset=appended.get(key,0);effective=path+[len(parent)+offset]
            if any(effective[:min(len(effective),len(prior))]==prior[:min(len(effective),len(prior))] for prior in paths):
                raise ContractError('duplicate or overlapping contract edit writes: '+canonical(effective))
            paths.append(effective);appended[key]=offset+1
            target=tree
            for part in path:target=target[part]
            target.append(deepcopy(edit['value']))
            audit.append({'operation':'append','path':path,'previous_value_sha256':digest(canonical(parent).encode()),'new_value_sha256':digest(canonical(target).encode())})
            continue
        if any(path[:min(len(path),len(prior))]==prior[:min(len(path),len(prior))] for prior in paths):
            raise ContractError('duplicate or overlapping contract edit writes; replace a parent once with its complete value, or edit independent existing children: '+canonical(path))
        paths.append(path)
        for part in path[:-1]:
            if isinstance(parent,list) and type(part) is int and part<len(parent) or isinstance(parent,dict) and isinstance(part,str) and part in parent:parent=parent[part]
            else:raise ContractError('contract edit path does not resolve in the original base')
        key=path[-1]
        if isinstance(parent,list):
            if type(key) is not int or key>=len(parent) or edit['operation']!='replace':raise ContractError('list edits replace an existing exact index only')
            before=parent[key]
        elif isinstance(parent,dict) and isinstance(key,str):
            if (key in parent)!=(edit['operation']=='replace'):raise ContractError('replace requires an existing field; add requires an absent field')
            before=parent.get(key)
        else:raise ContractError('contract edit parent is not a map or list')
        target=tree
        for part in path[:-1]:target=target[part]
        target[key]=deepcopy(edit['value'])
        audit.append({'operation':edit['operation'],'path':path,'previous_value_sha256':digest(canonical(before).encode()),'new_value_sha256':digest(canonical(edit['value']).encode())})
    content=render_tree(tree,base['kind']);parse(content,base['kind'])
    result=deepcopy(response);result['artifact']={'kind':base['kind'],'format':'constructor','content':content}
    return result,{'policy':'exact-contract-field-edits/v1','base_material_id':base['material_id'],'base_sha256':base['sha256'],
        'original_sha256':digest(canonical(artifact).encode()),'derived_sha256':digest(content.encode()),'edits':audit,
        'scope':'only model-declared field edits on this pinned candidate; unedited values retained, no evidence/constraint/numeric repair or check promotion by host'}
