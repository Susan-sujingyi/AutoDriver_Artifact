"""Bounded context reselection over the same pinned original evidence pool.

Indices describe unresolved questions and locations, never semantic assertions.
Raw device source, configuration, normative parents and diagnostics stay intact.
"""
from copy import deepcopy
import json
import re

from .protocol.assembler import canonical


GROUPS=(('analysis',{'source_analysis'},'source_index'),
        ('documents',{'document_extract','device_document'},'document_index'),
        ('framework',{'framework_source'},'framework_index'))


def source_index(text):
    from .context_tables import expand,SCHEMA
    data=json.loads(text)
    if isinstance(data,dict) and data.get('schema') in {SCHEMA,'autodriver.lossless-record-tables/v1'}:data=expand(data)
    graph=data.get('program_dependencies') if isinstance(data,dict) else None
    if not isinstance(graph,dict) or not isinstance(graph.get('functions'),dict):return None
    functions={key:{k:value[k] for k in ('name','material_id','start_line','end_line','parameters') if k in value}
        for key,value in graph['functions'].items()}
    calls=[{k:call[k] for k in ('caller','callee','targets','material_id','line') if k in call}
        for call in graph.get('calls',[])]
    return {'functions':functions,'calls':calls,'configuration':graph.get('configuration'),
        'gaps':graph.get('gaps',[]),'excluded':graph.get('excluded',[]),'syntax_gaps':data.get('gaps',[]),
        'build_membership':data.get('build_membership',[]),'source_api_retrieval':data.get('source_api_retrieval'),
        'complete_graph_query':'slice_device_effects with exact function roots; the full graph and original source remain available'}


def document_pages(text):
    lines=text.splitlines();starts=[]
    for number,line in enumerate(lines,1):
        match=re.fullmatch(r'\[Original PDF page ([1-9][0-9]*)\]',line)
        if match:starts.append((int(match[1]),number))
    return [{'page':page,'start_line':line,'end_line':starts[i+1][1]-1 if i+1<len(starts) else len(lines)}
        for i,(page,line) in enumerate(starts)]


def reselect(request,evidence,register,number,*,protected_materials=()):
    """Return one complete index scope or None; never clip any material text."""
    for group,kinds,index_kind in GROUPS:
        selected=[item for item in request['selections'] if evidence.registry[item['material_id']]['kind'] in kinds
            and item['material_id'] not in protected_materials]
        if not selected:continue
        records=[];total=0
        for item in selected:
            entry=evidence.registry[item['material_id']]
            raw=evidence.read(item['material_id'])
            try:text=raw.decode('utf8')
            except UnicodeDecodeError:continue
            lines=text.splitlines(keepends=True);start=item.get('start_line',1);end=item.get('end_line',len(lines))
            record={'material_id':item['material_id'],'sha256':entry['sha256'],'selected_lines':[start,end],
                'total_lines':len(lines),'kind':entry['kind'],'virtual_path':entry['path']}
            if group=='analysis':
                try:view=source_index(text)
                except (ValueError,TypeError,KeyError):view=None
                if view is None:continue
                record['index']=view
            elif group=='documents':record['pages']=document_pages(text)
            else:
                from .evidence import SourceIndex
                index=SourceIndex(evidence)
                try:index.add(item['material_id'])
                except (ValueError,TypeError):continue
                record['declarations']=[{k:vars(symbol)[k] for k in ('name','start_line','end_line','syntax_kind','calls','references') if k in vars(symbol)}
                    for symbol in index.symbols.values()]
                record['gaps']=index.gaps
            records.append((item,record));total+=len(''.join(lines[start-1:end]).encode())
        if not records:continue
        index_data={'schema':'autodriver.context-scope-index/v1','index_kind':index_kind,
            'facts_available':False,'global_goal_unchanged':True,'complete_materials':[record for _,record in records],
            'required_next_action':'Request complete original passages/functions for concrete unresolved semantic questions; indices cannot support assertions.',
            'preserved':'all raw device source/configuration, normative FP/DBC/BP, candidates, integration requirements and diagnostics remain selected',
            'query_pool':'the same approved frozen originals; source hashes and provenance are unchanged'}
        text=canonical(index_data)+'\n'
        if len(text.encode())>=total:continue
        identifier=request['call_id']+'.scope.'+str(number)+'.'+group
        role=records[0][0]['role']
        # All members of a group must retain one allowed role. Different-role
        # evidence stays untouched rather than being silently reclassified.
        records=[(item,record) for item,record in records if item['role']==role]
        if not records:continue
        index_data['complete_materials']=[record for _,record in records];text=canonical(index_data)+'\n'
        parents=list(dict.fromkeys(item['material_id'] for item,_ in records))
        role_bytes=0
        for item,_ in records:
            lines=evidence.read(item['material_id']).decode('utf8').splitlines(keepends=True)
            role_bytes+=len(''.join(lines[item.get('start_line',1)-1:item.get('end_line',len(lines))]).encode())
        if len(text.encode())>=role_bytes:continue
        os_roles={evidence.registry[parent]['os_role'] for parent in parents}
        register(identifier,identifier+'.json',index_kind,role,text,parents,
            os_role=next(iter(os_roles)) if len(os_roles)==1 else 'shared')
        result=deepcopy(request);removed={id(item) for item,_ in records}
        result['selections']=[deepcopy(item) for item in request['selections'] if id(item) not in removed]
        result['selections'].append({'role':role,'material_id':identifier})
        result['goal']+=' 固定上下文上限触发完整索引范围重选；全局目标不变，先补取未决问题所需完整原文，不得把索引作为事实。'
        return result,{'group':group,'index_kind':index_kind,'index_material_id':identifier,
            'originals':parents,'original_selection_bytes':role_bytes,'index_bytes':len(text.encode()),
            'new_upstream_requests':0,'scope':'explicit non-factual index and unchanged original pool; no source/config/contract/candidate/diagnostic clipping'}
    return None
