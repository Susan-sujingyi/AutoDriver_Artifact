"""Extract cumulative code edits; contract/evidence claims stay advisory.

Only actual edit structure, host-owned paths and immutable baseline identities
are checked here. Compilation and behavior are separate later observations.
"""
import json
from pathlib import PurePosixPath

from .calls import decode_response
from .protocol.schemas import CODE
from .protocol.assembler import ProtocolError, schema_check, path_allowed, digest


def single_source_target(request):
    paths=request.get('editable_paths',[])
    # An auxiliary directory does not change the unique explicit file. A raw
    # source fence always targets that file; auxiliary edits require JSON.
    exact=[path for path in paths if not path.endswith('/') and not any(c in path for c in '*?[')]
    if len(exact)!=1:return None
    path=exact[0];suffix=PurePosixPath(path).suffix
    if suffix not in {'.c','.h','.rs'}:return None
    base=request.get('base_files',{}).get(path)
    return dict(path=path,language='rust' if suffix=='.rs' else 'c',
        operation='replace' if base is not None else 'create',base_sha256=base)


def source_fence(text,request):
    target=single_source_target(request)
    if target is None or not text.startswith('```') or '\n' not in text:return None
    opening,rest=text.split('\n',1);language=opening[3:].strip().lower()
    if language not in {'',target['language']} or not rest.endswith('```'):return None
    body=rest[:-3]
    if body and not body.endswith('\n'):return None
    if any(line.startswith('```') for line in body.splitlines()):return None
    # Preserve historical unlabelled JSON fences as JSON, not source text.
    if not language and body.lstrip().startswith('{'):return None
    artifact=dict(kind='code',format='file_edits',edits=[dict(
        path=target['path'],operation=target['operation'],base_sha256=target['base_sha256'],content=body)])
    return artifact,dict(carrier='host_bound_single_source_fence',target=target,
        source_content_normalized=False)


def repair_outer_artifact_closers(text):
    """Remove only redundant outer closers after a complete code artifact.

    All prefix values and the artifact must parse first. The entire repaired
    response must then pass the ordinary strict decoder. Never repair or
    unescape source content, select a nested example, or discard malformed tail.
    """
    decoder=json.JSONDecoder();position=0
    def space(at):
        while at<len(text) and text[at].isspace():at+=1
        return at
    position=space(position)
    if position>=len(text) or text[position]!='{':return None
    position+=1;keys=set()
    try:
        while position<len(text):
            key,end=decoder.raw_decode(text,space(position))
            if not isinstance(key,str) or key in keys:return None
            keys.add(key);position=space(end)
            if text[position:position+1]!=':':return None
            value,end=decoder.raw_decode(text,space(position+1))
            if key=='artifact':
                if not isinstance(value,dict) or value.get('kind')!='code':return None
                after=end;closers=0
                while after<len(text) and (text[after].isspace() or text[after] in ']}'):
                    if text[after] in ']}':closers+=1
                    after+=1
                if not closers or text[after:after+1]!=',':return None
                fixed=text[:end]+text[after:]
                parsed,normalization=decode_response(fixed)
                if parsed.get('artifact')!=value:return None
                return parsed,dict(carrier='redundant_outer_artifact_closers',
                    removed_outer_text=text[end:after],artifact_source_unchanged=True,
                    repaired_response_sha256=digest(fixed.encode()),decoder=normalization)
            position=space(end)
            if text[position:position+1]!=',':return None
            position+=1
    except (ValueError,TypeError,RecursionError,ProtocolError):return None
    return None


def extract_code(raw, request):
    if not isinstance(raw,str):
        raise ProtocolError('bounded code response text required')
    try:
        raw_bytes=raw.encode()
    except UnicodeEncodeError:
        raise ProtocolError('code response must be UTF-8 text') from None
    if len(raw_bytes)>2_000_000:
        raise ProtocolError('bounded code response text required')
    text=raw.strip()
    fence=None
    direct=source_fence(text,request)
    if direct is not None:
        artifact,normalization=direct;value=artifact
    else:
        if text.startswith(('```json\n','```\n')) and text.endswith('\n```'):
            fence='single_outer_json_fence'
            text=text[text.index('\n')+1:-4]
        try:value,normalization=decode_response(text)
        except ProtocolError:
            repaired=repair_outer_artifact_closers(text)
            if repaired is None:raise
            value,normalization=repaired
    if not isinstance(value,dict):
        raise ProtocolError('code response needs one edit object')
    artifact=value.get('artifact',value)
    schema_check(artifact,CODE)
    if len(artifact['edits'])>128:
        raise ProtocolError('candidate edit count bound exceeded')
    touched=set()
    for edit in artifact['edits']:
        path,operation=edit['path'],edit['operation']
        if path in touched or not path_allowed(path,request):
            raise ProtocolError('duplicate or out-of-scope candidate edit')
        touched.add(path)
        baseline=request.get('base_files',{}).get(path)
        if operation=='create':
            if baseline is not None or edit['base_sha256'] is not None or not isinstance(edit['content'],str):
                raise ProtocolError('create must target a missing baseline file')
        elif baseline is None or edit['base_sha256']!=baseline:
            raise ProtocolError('replace/delete baseline identity missing or changed')
        elif operation=='delete' and edit['content'] is not None:
            raise ProtocolError('delete content must be null')
        elif operation=='replace' and not isinstance(edit['content'],str):
            raise ProtocolError('replace needs complete source text')
        if isinstance(edit['content'],str):
            try:
                size=len(edit['content'].encode())
            except UnicodeEncodeError:
                raise ProtocolError('candidate source must be UTF-8 text') from None
            if size>10_000_000:
                raise ProtocolError('candidate source file exceeds size bound')
    return artifact,dict(schema='autodriver.document-code-check/v1',
                         raw_response_sha256=digest(raw.encode()),
                         status='edit_scope_checked',edited_paths=sorted(touched),
                         envelope_normalization=normalization,outer_fence=fence,
                         original_envelope_metadata={key:value[key] for key in ('call_id','stage','outcome') if key in value},
                         semantic_validation='not_checked',compile_status='not_run',
                         independent_scoring='not_run',hardware_status='not_run')
