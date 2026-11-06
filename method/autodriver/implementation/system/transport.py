"""Lossless JSON tree carrier for the existing bounded constructor language."""
import re
from .contract.model import ContractError
from .protocol.language_catalog import load_catalog


def render_tree(value,kind):
    catalog=load_catalog();count=0
    def render(item,depth=0):
        nonlocal count
        count+=1
        if count>50000 or depth>80:raise ContractError('constructor tree size/depth bound exceeded')
        child=lambda value:render(value,depth+1)
        if item is None or type(item) in {str,int,bool}:return repr(item)
        if isinstance(item,list):return '['+', '.join(map(child,item))+']'
        if not isinstance(item,dict):raise ContractError('tree numbers use integers or an explicit $decimal literal; no implicit binary64 conversion')
        if '$constructor' in item:
            name=item['$constructor']
            if not isinstance(name,str) or name not in catalog or catalog[name]['language'] not in {'shared',kind}:
                raise ContractError('unknown tree constructor or layer: '+str(name))
            if 'args' in item and 'fields' in item:
                if set(item)!={'$constructor','args','fields'}:raise ContractError('legacy constructor tree needs exactly $constructor,args,fields')
                args=item['args'];fields=item['fields']
            else:
                args=[];fields={key:value for key,value in item.items() if key!='$constructor'}
                variadic=catalog[name]['varargs']
                if variadic and variadic in fields:
                    args=fields.pop(variadic)
            if not isinstance(args,list) or not isinstance(fields,dict) or any(not isinstance(key,str) or not key.isidentifier() for key in fields):
                raise ContractError('constructor tree args/fields are malformed')
            return name+'('+', '.join([*(child(arg) for arg in args),*(key+'='+child(value) for key,value in fields.items())])+')'
        if '$decimal' in item:
            literal=item['$decimal']
            if set(item)!={'$decimal'} or not isinstance(literal,str) or len(literal)>512 or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,4})?',literal):
                raise ContractError('explicit finite decimal tree literal required')
            return literal
        if '$tuple' in item:
            if set(item)!={'$tuple'} or not isinstance(item['$tuple'],list):raise ContractError('malformed tuple carrier')
            return '('+', '.join(map(child,item['$tuple']))+(',' if len(item['$tuple'])==1 else '')+')'
        if '$map' in item:
            pairs=item['$map']
            if set(item)!={'$map'} or not isinstance(pairs,list) or any(not isinstance(pair,list) or len(pair)!=2 for pair in pairs):raise ContractError('malformed map carrier')
            return '{'+', '.join(child(k)+': '+child(v) for k,v in pairs)+'}'
        if any(not isinstance(key,str) for key in item):raise ContractError('JSON map keys must be strings; use $map for explicit non-string keys')
        return '{'+', '.join(repr(key)+': '+child(value) for key,value in item.items())+'}'
    if not isinstance(value,dict) or '$constructor' not in value:raise ContractError('constructor tree root must be a constructor node')
    result=render(value)
    if len(result.encode())>2_000_000:raise ContractError('rendered constructor tree exceeds text bound')
    return result


def resolve_citations(response,messages):
    """Resolve only exact visible excerpt handles; infer no assertion support."""
    from copy import deepcopy
    import json
    from .protocol.assembler import canonical,digest
    links=response.get('evidence_links',[]) if isinstance(response,dict) else []
    if not isinstance(links,list) or any(not isinstance(link,dict) for link in links):raise ContractError('evidence_links must be a list of link objects')
    if not links:return response,None
    context=json.loads(messages[-1]['content'])['context']
    handles={};resolved=[];audit=[]
    for item in context:
        if 'citation_id' not in item:continue
        handle=item['citation_id']
        if handle in handles and any(handles[handle][key]!=item[key] for key in ('material_id','sha256','start_line','end_line')):
            raise ContractError('ambiguous provided citation handle')
        handles[handle]=item
    for link in links:
        if 'citations' not in link:
            matching=[item for item in context if item.get('material_id')==link.get('material_id')
                and type(link.get('start_line')) is int and type(link.get('end_line')) is int
                and item['start_line']<=link['start_line']<=link['end_line']<=item['end_line']]
            if matching and not any(item.get('evidence_eligible',True) for item in matching):
                raise ContractError('evidence citation is not eligible for this contract stage')
            resolved.append(link);continue
        if set(link)!={'claim','basis','citations'} or not isinstance(link['citations'],list) or not 1<=len(link['citations'])<=32:
            raise ContractError('citation link needs claim,basis and1..32 exact visible handles')
        for handle in link['citations']:
            if not isinstance(handle,str) or handle not in handles:raise ContractError('unknown or unprovided citation handle: '+str(handle))
            entry=handles[handle]
            if not entry.get('evidence_eligible',True):raise ContractError('provided citation is not eligible for this contract stage: '+handle)
            resolved.append({'claim':link['claim'],'basis':link['basis'],'material_id':entry['material_id'],
                'start_line':entry['start_line'],'end_line':entry['end_line']})
            audit.append({'citation_id':handle,'material_id':entry['material_id'],'sha256':entry['sha256'],
                'start_line':entry['start_line'],'end_line':entry['end_line']})
        if len(resolved)>512:raise ContractError('resolved citation bound exceeded')
    normalized=deepcopy(response);normalized['evidence_links']=resolved
    return normalized,{'policy':'exact-excerpt-handles/v1','original_sha256':digest(canonical(links).encode()),'resolved':audit,
        'scope':'same claim/basis attached to exact provided excerpts; source entailment remains a separate check'}
