"""Bounded constructor-call delimiter completion, with no value edits.

Only missing ')' before an existing enclosing delimiter can be inserted. The
unclosed frames must be known constructor calls. EOF completion, tuple/list/
dictionary completion, deletion and replacement are deliberately unsupported.
"""
import ast
from copy import deepcopy

from .contract.model import parse,definitions,ContractError
from .protocol.assembler import digest


def complete_call_delimiters(text,kind,*,maximum_inserted=8):
    if not isinstance(text,str) or len(text.encode())>2000000:raise ContractError('bounded constructor text required')
    try:ast.parse(text,mode='eval');return text,None
    except (SyntaxError,ValueError,RecursionError):pass
    matching={')':'(',']':'[','}':'{'};stack=[];edits=[];i=0;quote=None;catalog=definitions()
    while i<len(text):
        value=text[i]
        if quote:
            if value=='\\':i+=2;continue
            if text.startswith(quote,i):i+=len(quote);quote=None;continue
            i+=1;continue
        if value in {'"',"'"}:
            quote=value*3 if text.startswith(value*3,i) else value;i+=len(quote);continue
        if value=='#':
            newline=text.find('\n',i);i=len(text) if newline<0 else newline+1;continue
        if value in '([{':
            end=i
            while end and text[end-1].isspace():end-=1
            start=end
            while start and (text[start-1].isalnum() or text[start-1]=='_'):start-=1
            stack.append((value,value=='(' and text[start:end] in catalog))
            if len(stack)>80:raise ContractError('delimiter depth bound exceeded')
        elif value in matching:
            opener=matching[value]
            if not any(item[0]==opener for item in stack):raise ContractError('unexpected closing delimiter')
            added=''
            while stack[-1][0]!=opener:
                if stack[-1]!=('(',True):raise ContractError('only constructor-call parentheses can be completed')
                stack.pop();added+=')'
            stack.pop()
            if added:edits.append({'character_offset':i,'insert':added})
        i+=1
    if quote or stack:raise ContractError('unterminated literals or EOF delimiters require model repair')
    if not edits or sum(len(item['insert']) for item in edits)>maximum_inserted:
        raise ContractError('no admissible bounded call-delimiter completion')
    completed=text
    for edit in reversed(edits):
        index=edit['character_offset'];completed=completed[:index]+edit['insert']+completed[index:]
    parse(completed,kind)  # Complete binding/arity/catalog checks, not just ast.parse.
    return completed,{'policy':'constructor-call-delimiter-completion/v1','edits':edits,
        'original_sha256':digest(text.encode()),'derived_sha256':digest(completed.encode()),
        'scope':'only closing parentheses inserted; literal and identifier tokens preserved; semantic checks still required'}


def normalize_contract_response(response,*,messages=None):
    artifact=response.get('artifact') if isinstance(response,dict) else None
    if isinstance(artifact,dict) and artifact.get('format')=='constructor_edits':
        from .contract_patch import apply_response
        return apply_response(response,messages)
    if isinstance(artifact,dict) and artifact.get('kind') in {'fp','dbc','bp'} and artifact.get('format')=='constructor_tree':
        from .transport import render_tree
        from .protocol.assembler import canonical
        content=render_tree(artifact['content'],artifact['kind'])
        normalized=deepcopy(response);normalized['artifact']=dict(artifact,format='constructor',content=content)
        return normalized,{'policy':'constructor-json-tree-carrier/v1','original_sha256':digest(canonical(artifact['content']).encode()),
            'derived_sha256':digest(content.encode()),'scope':'lossless carrier rendering only; names/fields/literals retained and the ordinary parser/checkers still apply'}
    if not isinstance(artifact,dict) or artifact.get('kind') not in {'fp','dbc','bp'} or artifact.get('format')!='constructor':
        return response,None
    try:content,audit=complete_call_delimiters(artifact['content'],artifact['kind'])
    except (ContractError,TypeError,KeyError):return response,None
    if audit is None:return response,None
    normalized=deepcopy(response);normalized['artifact']['content']=content
    return normalized,audit
