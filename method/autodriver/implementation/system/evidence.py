"""Pinned evidence, syntax-based source facts, and bounded dependency selection.

Only registered public files are read. No benchmark or research imports exist.
Syntax facts deliberately retain unresolved preprocessing/provider conditions.
"""
from __future__ import annotations

from collections import defaultdict, deque
from bisect import bisect_right
from dataclasses import dataclass, asdict
import hashlib
from pathlib import Path
import subprocess
import tempfile

from .protocol.assembler import read_pinned, virtual_parts
from .contract.model import ContractError, Unknown


@dataclass(frozen=True)
class Symbol:
    identity: str
    name: str
    path: str
    source_sha256: str
    start_line: int
    end_line: int
    syntax_kind: str
    calls: tuple
    references: tuple
    conditions: tuple
    enclosing: tuple = ()


def descendants(node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


class SourceLocations:
    """Exact 1-based lines from original bytes, avoiding unsafe Point accessors.

    py-tree-sitter 0.26.0 has an upstream Point.row/column reference-count bug
    (issues #472/#500). Byte offsets are sufficient and keep the pinned runtime.
    """
    def __init__(self,source):
        self.length=len(source)
        self.starts=[0]+[i+1 for i,byte in enumerate(source) if byte==10]

    def line(self,offset):
        if not 0<=offset<=self.length:raise ContractError('source byte offset out of bounds')
        return bisect_right(self.starts,offset)


class Evidence:
    def __init__(self, root, registry):
        self.root = Path(root)
        self.registry = {entry['id']:dict(entry) for entry in registry['materials']}
        if len(self.registry) != len(registry['materials']):
            raise ContractError('duplicate evidence material IDs')

    def read(self, material_id):
        entry = self.registry.get(material_id)
        if entry is None or not entry['approved']:
            raise ContractError('unregistered/unapproved material')
        # Reuse the same source-boundary validator as message assembly before
        # exposing a registered file through a tool, including all ancestors.
        self._lineage(material_id,set())
        return read_pinned(self.root,entry)

    def _lineage(self, material_id, pending):
        if material_id in pending: raise ContractError('cyclic evidence lineage')
        entry = self.registry.get(material_id)
        from .protocol.assembler import PROTOCOLS
        allowed = set().union(*map(set,PROTOCOLS['role_kinds'].values()))
        if entry is None or not entry['approved'] or entry['kind'] not in allowed:
            raise ContractError('forbidden or missing evidence ancestor')
        if entry['kind'] in PROTOCOLS['derived_kinds'] and not entry['parents']:
            raise ContractError('derived evidence has no original parents')
        if entry['kind'] == 'internal_diagnostic' and entry.get('producer') not in PROTOCOLS['diagnostic_producers']:
            raise ContractError('unapproved diagnostic producer')
        for parent in entry['parents']:
            self._lineage(parent,pending|{material_id})
            read_pinned(self.root,self.registry[parent])

    def excerpt(self, material_id, start, end, *, maximum_lines=500):
        if type(start) is not int or type(end) is not int or not 1 <= start <= end or end-start+1 > maximum_lines:
            raise ContractError('bounded excerpt coordinates required')
        lines = self.read(material_id).decode('utf8').splitlines(keepends=True)
        if end > len(lines): raise ContractError('excerpt outside original evidence')
        entry=self.registry[material_id]
        return {'material_id':material_id,'sha256':entry['sha256'],'start_line':start,'end_line':end,
                'text':''.join(lines[start-1:end]),'origin_locator':entry.get('origin_locator','')}

    def search(self, material_ids, terms, *, maximum_results=16, radius=4):
        if not 1 <= maximum_results <= 32 or not 0 <= radius <= 10 or len(material_ids)>256:
            raise ContractError('retrieval limits exceeded')
        if not terms or len(terms)>16 or any(not isinstance(x,str) or not x or len(x)>128 for x in terms):
            raise ContractError('bounded literal search terms required')
        candidates=[]
        for material_id in material_ids:
            data=self.read(material_id)
            try: lines=data.decode('utf8').splitlines()
            except UnicodeDecodeError: continue
            for i,line in enumerate(lines):
                score=sum(term.casefold() in line.casefold() for term in terms)
                if score:
                    candidates.append((score,material_id,max(1,i+1-radius),min(len(lines),i+1+radius)))
        candidates.sort(key=lambda x:(-x[0],x[1],x[2]))
        excerpts=[];seen=set()
        for _,material_id,start,end in candidates:
            if (material_id,start,end) not in seen:
                seen.add((material_id,start,end));excerpts.append(self.excerpt(material_id,start,end))
            if len(excerpts)>=maximum_results: break
        return {'excerpts':excerpts,'complete':False,'scope':'literal evidence retrieval; selected passages may need cross-reference/footnote expansion'}


class SourceIndex:
    def __init__(self,evidence):
        self.evidence=evidence
        self.symbols={}
        self.by_name=defaultdict(list)
        self.materials={}
        self.gaps=[]
        self.parsers={}
        self.excluded=[]
        self.declaration_symbols=set()
        self.macro_declaration_blocks={}

    def add(self,material_id,*,maximum_bytes=2_000_000):
        entry=self.evidence.registry[material_id]
        if entry['kind'] not in {'device_source','framework_source'}:
            raise ContractError('source index requires registered original source')
        source=self.evidence.read(material_id)
        if len(source)>maximum_bytes: raise Unknown('source parser byte bound exceeded')
        suffix=Path(entry['path']).suffix
        from tree_sitter import Language,Parser
        if suffix in {'.c','.h'}:
            import tree_sitter_c
            language=Language(tree_sitter_c.language())
        elif suffix == '.rs':
            import tree_sitter_rust
            language=Language(tree_sitter_rust.language())
        else:
            raise Unknown('source grammar is not supported')
        if suffix not in self.parsers:self.parsers[suffix]=Parser(language)
        tree=self.parsers[suffix].parse(source)
        locations=SourceLocations(source)
        excluded_ranges=[]
        if suffix=='.rs':
            for attribute in descendants(tree.root_node):
                if attribute.type!='attribute_item':continue
                value=source[attribute.start_byte:attribute.end_byte].decode('utf8')
                compact=''.join(value.split())
                following=attribute.next_named_sibling
                while following is not None and following.type=='attribute_item':following=following.next_named_sibling
                if following is None:continue
                if compact in {'#[cfg(test)]','#[test]'}:
                    excluded_ranges.append((following.start_byte,following.end_byte))
                    self.excluded.append({'material_id':material_id,'sha256':entry['sha256'],
                        'start_line':locations.line(attribute.start_byte),'end_line':locations.line(following.end_byte-1),
                        'reason':'Rust test-only declaration excluded for the non-test public build configuration'})
                elif compact.startswith('#[cfg('):
                    self.gaps.append({'material_id':material_id,'line':locations.line(attribute.start_byte),
                                      'reason':'Rust feature/target cfg requires selected build configuration: '+value})
        self.materials[material_id]={'sha256':entry['sha256'],'path':entry['path'],'syntax_errors':tree.root_node.has_error}
        if tree.root_node.has_error:
            self.gaps.append({'material_id':material_id,'reason':'syntax errors/macros require configuration-aware frontend'})
        if suffix in {'.c','.h'} and entry['kind']=='framework_source':
            self._framework_data(material_id, entry, source, tree, locations)
        function_types={'function_definition','function_item','function_signature_item',
                        'declaration','field_declaration','preproc_def','preproc_function_def',
                        'struct_specifier','enum_specifier','enumerator','type_definition',
                        'trait_item','const_item','type_item','enum_item','struct_item'}
        for item in descendants(tree.root_node):
            if item.type not in function_types: continue
            if any(start<=item.start_byte and item.end_byte<=end for start,end in excluded_ranges):continue
            if item.type in {'declaration','field_declaration'} and not any(
                    n.type=='function_declarator' for n in descendants(item)):
                continue
            declaration=item.child_by_field_name('declarator')
            name=item.child_by_field_name('name')
            if name is None and declaration is not None:
                current=declaration
                while current is not None:
                    if current.type in {'identifier','field_identifier','type_identifier'}:
                        name=current;break
                    current=current.child_by_field_name('declarator')
            if name is None:
                self.gaps.append({'material_id':material_id,'line':locations.line(item.start_byte),'reason':'function name not resolved'})
                continue
            calls=[];references=set();conditions=[]
            for part in descendants(item):
                if part.type in {'call_expression','macro_invocation'}:
                    called=part.child_by_field_name('function') or part.child_by_field_name('macro')
                    if called is not None:
                        calls.append(source[called.start_byte:called.end_byte].decode('utf8'))
                if part.type in {'identifier','type_identifier','field_identifier'}:
                    references.add(source[part.start_byte:part.end_byte].decode('utf8'))
                if part.type in {'if_statement','if_expression','while_statement','while_expression','match_expression','switch_statement'} or part.type.startswith('preproc_if'):
                    condition=part.child_by_field_name('condition') or part.child_by_field_name('value')
                    conditions.append({'line':locations.line(part.start_byte),'kind':part.type,'condition':source[condition.start_byte:condition.end_byte].decode('utf8') if condition else 'unresolved'})
            parent=item.parent
            enclosing=[]
            while parent is not None:
                if parent.type.startswith('preproc_if'):
                    conditions.append({'line':locations.line(parent.start_byte),'kind':parent.type,'condition':'enclosing preprocessing branch unresolved'})
                if parent.type in {'impl_item','trait_item','mod_item'}:
                    body=parent.child_by_field_name('body')
                    end=body.start_byte if body is not None else parent.end_byte
                    enclosing.append({'kind':parent.type,'start_line':locations.line(parent.start_byte),
                        'end_line':locations.line(max(parent.start_byte,end-1)),
                        'header':source[parent.start_byte:end].decode('utf8')})
                parent=parent.parent
            symbol_name=source[name.start_byte:name.end_byte].decode('utf8')
            start_line=locations.line(item.start_byte);end_line=locations.line(max(item.start_byte,item.end_byte-1))
            sid=f"{material_id}:{start_line}:{symbol_name}"
            if sid in self.symbols: continue
            symbol=Symbol(sid,symbol_name,entry['path'],entry['sha256'],start_line,end_line,
                          item.type,tuple(calls),tuple(sorted(references)),tuple(conditions),tuple(reversed(enclosing)))
            self.symbols[sid]=symbol;self.by_name[symbol_name].append(sid)
            # A C struct/enum mention in a parameter is a type use, not a
            # declaration to emit. Its line can also contain a function body.
            # Preserve the legacy complete source index; ABI projection uses
            # this separate syntax-derived declaration inventory.
            local=False;ancestor=item.parent
            while ancestor is not None:
                if ancestor.type in {'function_definition','function_item'}:local=True;break
                ancestor=ancestor.parent
            declaration_kind=item.type in {'trait_item','const_item','type_item','enum_item','struct_item',
                'type_definition','preproc_def','preproc_function_def','enumerator','function_signature_item',
                'declaration','field_declaration'}
            if item.type in {'struct_specifier','enum_specifier'}:
                declaration_kind=item.child_by_field_name('body') is not None
                if (not declaration_kind and item.parent is not None and item.parent.type=='declaration'
                        and item.parent.child_by_field_name('declarator') is None):declaration_kind=True
            if declaration_kind and not local:self.declaration_symbols.add(sid)
            if not local and item.type in {'preproc_def','preproc_function_def'}:
                # Sibling macro declarations commonly encode an integer API
                # domain without a type reference. Keep their original syntax
                # block, never infer a name prefix, numeric range or validity.
                # A different declaration or preprocessing branch ends it.
                first=last=item
                while first.prev_named_sibling is not None and first.prev_named_sibling.type in {
                        'comment','preproc_def','preproc_function_def'}:
                    first=first.prev_named_sibling
                while last.next_named_sibling is not None and last.next_named_sibling.type in {
                        'comment','preproc_def','preproc_function_def'}:
                    last=last.next_named_sibling
                self.macro_declaration_blocks[sid]=(locations.line(first.start_byte),
                    locations.line(max(first.start_byte,last.end_byte-1)))
        return {'symbols':len(self.symbols),'gaps':self.gaps,'semantic_scope':'syntax only; no macro/type/provider proof'}

    def _framework_data(self, material_id, entry, source, tree, locations):
        """Index complete file-scope data consumed by public framework APIs.

        Metadata/dispatch tables are evidence, not API guarantees. Do not index
        local variables, device implementation data or evaluate cfg branches.
        Multiple declarators share the exact original declaration extent.
        """
        for item in descendants(tree.root_node):
            if item.type != 'declaration':
                continue
            parent = item.parent
            conditions = []
            while parent is not None and parent.type.startswith('preproc_'):
                conditions.append(dict(line=locations.line(parent.start_byte),
                    kind=parent.type, condition='enclosing preprocessing branch unresolved'))
                parent = parent.parent
            if parent is None or parent.type != 'translation_unit':
                continue
            for declarator in item.children_by_field_name('declarator'):
                current = declarator
                while current is not None and current.type not in {'identifier','function_declarator'}:
                    current = current.child_by_field_name('declarator')
                if current is None or current.type != 'identifier':
                    continue
                name = source[current.start_byte:current.end_byte].decode('utf8')
                line = locations.line(item.start_byte)
                sid = f'{material_id}:{line}:{name}'
                if sid in self.symbols:
                    continue
                references = tuple(sorted({source[n.start_byte:n.end_byte].decode('utf8')
                    for n in descendants(item)
                    if n.type in {'identifier','type_identifier','field_identifier'}}))
                self.symbols[sid] = Symbol(sid, name, entry['path'], entry['sha256'],
                    line, locations.line(max(item.start_byte,item.end_byte-1)),
                    'global_data_declaration', (), references, tuple(conditions))
                self.by_name[name].append(sid)
                self.declaration_symbols.add(sid)

    def closure(self,roots,*,maximum_symbols=64,maximum_lines=6000,include_callers=False):
        if not roots or len(roots)>64 or not 1<=maximum_symbols<=256 or not 1<=maximum_lines<=20000:
            raise ContractError('invalid dependency closure bounds')
        pending=deque((name,None) for name in roots);selected=set();dependencies=[];gaps=[];lines=0
        while pending:
            name,parent=pending.popleft()
            ids=[name] if name in self.symbols else self.by_name.get(name,[])
            if not ids:
                gaps.append({'query':name,'parent':parent,'reason':'unresolved symbol/provider/macro; request another approved source file'})
            if len(ids)>1:
                gaps.append({'query':name,'reason':'multiple declarations/providers retained; dispatch not proved'})
            for sid in ids:
                dependencies.append({'parent':parent,'symbol':sid})
                if sid in selected: continue
                sym=self.symbols[sid];span=sym.end_line-sym.start_line+1
                if len(selected)>=maximum_symbols or lines+span>maximum_lines:
                    gaps.append({'query':sid,'reason':'closure budget reached; context is incomplete'})
                    continue
                selected.add(sid);lines+=span
                pending.extend((call,sid) for call in sym.calls)
                if include_callers:
                    pending.extend((candidate.identity,sid) for candidate in self.symbols.values() if sym.name in candidate.calls)
        return {'roots':list(roots),'symbols':[asdict(self.symbols[sid]) for sid in sorted(selected)],
                'dependencies':dependencies,'lines':lines,'gaps':gaps,
                'stop':'budget_or_unresolved' if gaps else 'indexed_call_dependency_closure',
                'scope':'indexed syntax closure; not complete framework semantics or all dispatch targets'}

    def excerpts(self,roots,*,maximum_symbols=128,maximum_lines=6000,include_types=True,declarations_only=False):
        """Whole declarations/bodies plus attached docs, never clipped strings."""
        pending=deque((root,None) for root in roots);selected=set();dependencies=[];gaps=[];omitted=set()
        type_kinds={'struct_specifier','enum_specifier','type_definition','trait_item','const_item','type_item',
                    'enum_item','struct_item','preproc_def','preproc_function_def','enumerator',
                    'global_data_declaration'}
        while pending:
            name,parent=pending.popleft();ids=[name] if name in self.symbols else self.by_name.get(name,[])
            if not ids:gaps.append({'symbol':name,'parent':parent,'reason':'declaration/provider not indexed'})
            for sid in ids:
                dependencies.append({'parent':parent,'symbol':sid})
                if sid in selected:continue
                if len(selected)>=maximum_symbols:
                    if sid not in omitted:gaps.append({'symbol':sid,'reason':'symbol closure bound reached'});omitted.add(sid)
                    continue
                selected.add(sid);symbol=self.symbols[sid]
                if symbol.syntax_kind == 'trait_item' and not declarations_only:
                    # A relevant public trait includes lifecycle operations as
                    # well as I/O. Recover implementation candidates for its
                    # declared methods without assuming receiver dispatch.
                    members = {s.name for s in self.symbols.values()
                        if s.path == symbol.path and symbol.start_line <= s.start_line
                        and s.end_line <= symbol.end_line
                        and s.syntax_kind in {'function_signature_item','function_item'}}
                    for member in sorted(members):
                        pending.append((member,sid))
                        dependencies.append(dict(parent=sid, member=member,
                            resolution='all indexed trait method candidates; receiver dispatch not established'))
                # ABI projection follows API implementation dependencies to
                # discover enum/metadata domains, but emits no function body.
                # Trait roots already contain their full method signatures;
                # avoid unrelated implementation fanout for shared names.
                for call in (() if declarations_only and symbol.syntax_kind in type_kinds else symbol.calls):
                    name=call if call in self.by_name else call.rsplit('::',1)[-1].rsplit('.',1)[-1]
                    if name in self.by_name:
                        pending.append((name,sid))
                        if name!=call:dependencies.append({'parent':sid,'call':call,'resolved_name':name,
                            'resolution':'all indexed method-name candidates; receiver dispatch not established'})
                if symbol.syntax_kind == 'global_data_declaration' and not declarations_only:
                    for ref in symbol.references:
                        for target in self.by_name.get(ref, []):
                            if self.symbols[target].syntax_kind in {'function_definition','function_item'}:
                                pending.append((target, sid))
                                dependencies.append(dict(parent=sid, symbol=target,
                                    resolution='function reference in framework data; active dispatch not established'))
                if include_types:
                    pending.extend((target,sid) for ref in symbol.references for target in self.by_name.get(ref,[])
                                   if self.symbols[target].syntax_kind in type_kinds)
        ranges=defaultdict(list);total=0;units=[]
        path_ids={record['path']:id for id,record in self.materials.items()}
        for sid in sorted(selected,key=lambda s:(0 if s in roots or self.symbols[s].name in roots else 1,s)):
            symbol=self.symbols[sid]
            if declarations_only and sid not in self.declaration_symbols:continue
            material_id=path_ids[symbol.path]
            lines=self.evidence.read(material_id).decode('utf8').splitlines(keepends=True)
            start=symbol.start_line;end=symbol.end_line
            macro_block=self.macro_declaration_blocks.get(sid) if declarations_only else None
            if macro_block is not None:
                start,end=macro_block
            while start>1:
                previous=lines[start-2].lstrip()
                if not previous.strip() or previous.startswith(('//','/*','*','#[','///','//!')):start-=1
                else:break
            if any(a<=start and end<=b for a,b in ranges[material_id]):continue
            added=set(range(start,end+1))-set(n for a,b in ranges[material_id] for n in range(a,b+1))
            if total+len(added)>maximum_lines:
                gaps.append({'symbol':sid,'reason':'whole semantic unit exceeds remaining line budget; not truncated'});continue
            ranges[material_id].append((start,end));total+=len(added)
            unit={'symbol':sid,'material_id':material_id,'start_line':start,'end_line':end}
            if macro_block is not None:
                unit['projection']='adjacent macro declarations within one syntax parent; validity not inferred'
            units.append(unit)
        excerpts=[]
        for material_id,spans in ranges.items():
            merged=[]
            for start,end in sorted(spans):
                if merged and start<=merged[-1][1]+1:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
                else:merged.append((start,end))
            excerpts.extend({'material_id':material_id,'start_line':start,'end_line':end} for start,end in merged)
        return {'roots':list(roots),'selections':excerpts,'units':units,'dependencies':dependencies,'gaps':gaps,
                'lines':total,'stop':'bounded_declaration_type_closure' if declarations_only else 'bounded_symbol_type_call_closure',
                'complete':not gaps}


def extract_pdf(evidence,material_id,*,maximum_pages=512):
    entry=evidence.registry[material_id]
    if entry['kind'] != 'device_document': raise ContractError('original registered PDF required')
    data=evidence.read(material_id)
    import pymupdf
    with pymupdf.open(stream=data,filetype='pdf') as document:
        if len(document)>maximum_pages: raise Unknown('PDF page bound exceeded')
        pages=[]
        for number,page in enumerate(document,1):
            blocks=page.get_text('blocks',sort=True)
            pages.append({'page':number,'page_size':[page.rect.width,page.rect.height],
                          'blocks':[{'bbox':list(block[:4]),'text':block[4]} for block in blocks if block[6]==0],
                          'text':page.get_text('text',sort=True),'needs_ocr_or_image':not any(block[6]==0 for block in blocks)})
    return {'schema':'autodriver.pdf-extract/v1','parent':material_id,'original_sha256':entry['sha256'],
            'extractor':'PyMuPDF '+pymupdf.VersionBind,'pages':pages,
            'scope':'mechanical full-page text/coordinates; table/footnote/diagram semantics require original-page review'}


def clang_facts(evidence,material_id,configuration,*,timeout=30):
    """Preprocess/AST a self-contained registered translation unit, no source build.

    Header-dependent sources fail explicitly. The host may prepare an approved
    include closure separately; no shell or arbitrary compiler flags are accepted.
    """
    if evidence.registry[material_id]['kind'] not in {'device_source','framework_source'}:
        raise ContractError('registered source required')
    if not isinstance(configuration,dict) or len(configuration)>512:
        raise ContractError('bounded explicit preprocessor definitions required')
    import json
    flags=[]
    for key,value in configuration.items():
        if not key.isidentifier() or type(value) not in {bool,int}:
            raise ContractError('preprocessor definition must be an identifier and integer')
        # Kconfig false is undefined. Defining NAME=0 would select the wrong
        # branch for #ifdef NAME; explicit integer zero remains a definition.
        flags.append('-U'+key if value is False else f'-D{key}={int(value)}')
    source=evidence.read(material_id)
    with tempfile.TemporaryDirectory(prefix='autodriver-clang-') as temporary:
        path=Path(temporary)/'input.c';path.write_bytes(source)
        common=['clang-14','-x','c','-std=c11','-nostdinc',*flags,str(path)]
        stage='ast'
        try:
            ast_process=subprocess.run(common+['-fsyntax-only','-Xclang','-ast-dump=json'],capture_output=True,timeout=timeout)
            stage='preprocessing'
            preprocessed=subprocess.run(common+['-E','-P'],capture_output=True,timeout=timeout)
        except (subprocess.TimeoutExpired,FileNotFoundError) as error:
            # No partial AST or preprocessor output is admitted as a fact.
            # subprocess.run reaps a timed-out child before this result.
            gap='timeout' if isinstance(error,subprocess.TimeoutExpired) else 'tool_unavailable'
            return {'material_id':material_id,'sha256':hashlib.sha256(source).hexdigest(),
                'configuration':configuration,'ast':None,'preprocessed':None,'status':'unknown',
                'diagnostic':'Clang '+stage+' '+gap,
                'tool_gap':{'kind':gap,'stage':stage,'per_invocation_timeout_seconds':timeout},
                'scope':'source frontend did not complete; no branch, AST or device semantic conclusion'}
    return {'material_id':material_id,'sha256':hashlib.sha256(source).hexdigest(),'configuration':configuration,
            'ast':json.loads(ast_process.stdout) if ast_process.returncode==0 else None,
            'preprocessed':preprocessed.stdout.decode('utf8') if preprocessed.returncode==0 else None,
            'status':'facts_extracted' if ast_process.returncode==preprocessed.returncode==0 else 'unknown',
            'diagnostic':ast_process.stderr.decode('utf8').replace(str(path),'registered-input.c'),
            'scope':'Clang preprocessing/AST facts under explicit definitions; no device semantic proof'}
