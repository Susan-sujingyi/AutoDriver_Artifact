"""Explicit location indices for oversized original evidence, never summaries.

Whole original bytes stay registered and available to the finite original-text
and source-analysis tools. Normative drafts, config, candidates and diagnostics
are not clipped or replaced. Each replacement is recorded before model input.
"""
import json

from .protocol.assembler import canonical, digest
from .context_scope import document_pages


GROUPS = {
    'device_source':('source_index','source_facts'),
    'source_analysis':('source_index','source_facts'),
    'document_extract':('document_index','document_evidence'),
    'framework_source':('framework_index','framework_evidence'),
    'framework_document':('framework_index','framework_evidence'),
}


def compact_originals(workspace, selections, call_id, *, threshold=65536, protected=()):
    result, audit = [], []
    evidence = workspace.evidence()
    for number, selected in enumerate(selections):
        entry = workspace.entries[selected['material_id']]
        if (entry['kind'] not in GROUPS or selected['material_id'] in protected
                or selected['role'] in {'candidate','target_code'}):
            result.append(dict(selected))
            continue
        raw = evidence.read(selected['material_id']).decode('utf8')
        lines = raw.splitlines(keepends=True)
        start, end = selected.get('start_line',1), selected.get('end_line',len(lines))
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise ValueError('original selection bounds invalid')
        extent = ''.join(lines[start-1:end])
        if len(extent.encode()) <= threshold:
            result.append(dict(selected))
            continue
        kind, role = GROUPS[entry['kind']]
        index = dict(schema='autodriver.original-location-index/v1',facts_available=False,
                     material_id=selected['material_id'],sha256=entry['sha256'],
                     virtual_path=entry['path'],original_kind=entry['kind'],
                     requested_lines=[start,end],total_lines=len(lines),
                     original_bytes=len(raw.encode()),
                     source_scope='Same complete approved originals; this index establishes no device/framework facts.',
                     retrieval='Use read_evidence_range for complete original passages, inspect_symbol for named declarations/call closure, or slice_device_effects for whole function control/data closure. Device PDF text supports retrieve_device_evidence and original page ranges.')
        if entry['kind']=='document_extract':
            index['original_pdf_pages'] = document_pages(raw)
        elif entry['kind']=='source_analysis':
            try:
                data = json.loads(raw)
                functions = data.get('program_dependencies',{}).get('functions',{})
                directory = {key:{name:value[name] for name in ('name','material_id','start_line','end_line') if name in value}
                             for key,value in functions.items()}
                index['indexed_function_count'] = len(directory)
                if len(canonical(directory).encode())<=32768:
                    index['complete_function_directory'] = directory
                else:
                    index['complete_function_directory'] = None
                    index['directory_scope'] = 'Directory exceeds its own display bound; no partial list or absence claim. Query original source ranges or exact symbols.'
                index['mechanical_analysis_identity'] = digest(raw.encode())
                index['analysis_coverage'] = 'Control/data dependencies and every analysis gap remain in this exact parent. An index is not the analysis or a proof.'
            except (ValueError,TypeError,AttributeError):
                index['analysis_coverage'] = 'No supported directory projection; original source and analysis identity remain available.'
        text = canonical(index)+'\n'
        if len(text.encode())>=len(extent.encode()):
            result.append(dict(selected))
            continue
        material_id = call_id+'.original-index.'+str(number)
        workspace.add_derived(material_id,material_id+'.json',kind,role,text,
                              [selected['material_id']],os_role=entry['os_role'])
        result.append(dict(role=role,material_id=material_id))
        audit.append(dict(original_selection=dict(selected),index_material_id=material_id,
                          original_selection_bytes=len(extent.encode()),index_bytes=len(text.encode()),
                          raw_original_unchanged=True,facts_available=False))
    return result,audit
