"""Optional executable views of primary semantic documents.

This adapter never repairs a document or opens a generation gate. A parsed
view is useful input to the existing finite checkers, not evidence that its
meaning follows from the primary document or original sources.
"""
import json
import re

from .contract.model import ContractError
from .contract.check import check_contract
from .protocol.assembler import digest


def executable_view(raw, kind):
    if not isinstance(raw, str) or len(raw.encode()) > 2_000_000:
        raise ContractError('bounded primary document text required')
    candidates = []
    text = raw.strip()
    # These are explicit carriers, not a search for arbitrary code to execute.
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value, dict):
        artifact = value.get('artifact', value)
        if isinstance(artifact, dict) and artifact.get('kind') == kind and isinstance(artifact.get('content'), str):
            candidates.append(artifact['content'])
        view = value.get('executable_projection')
        if isinstance(view, str):
            candidates.append(view)
    candidates += re.findall(r'^```autodriver-projection\s*\n(.*?)\n```\s*$', text, re.M | re.S)
    if not candidates:
        candidates = [text]
    if len(candidates) != 1:
        return None, dict(status='unsupported', reason='ambiguous executable projection carriers',
                          primary_sha256=digest(raw.encode()), entailment='unverified')
    content = candidates[0]
    try:
        root, report = check_contract(content, kind)
    except ContractError as error:
        return None, dict(status='unsupported', reason=str(error),
                          primary_sha256=digest(raw.encode()), entailment='unverified')
    execution={}
    if kind=='dbc':
        from .contract.fsm import lower
        try:
            graph=lower(root['behavior'])
            execution=dict(execution_capability='partial' if graph.gaps else 'lowerable',execution_gaps=graph.gaps)
        except ContractError as error:
            execution=dict(execution_capability='unsupported',execution_reason=str(error))
    return content, dict(status='available', primary_sha256=digest(raw.encode()),
                         projection_sha256=digest(content.encode()), checks=report,
                         **execution,
                         entailment='unverified',
                         scope='deterministic checks of this derived view only; no generation admission or primary-document correctness claim')


def project_native_state(state, *, binding_plan):
    materials = {item['id']: item for item in state['materials']}
    projected = dict(state, materials=list(state['materials']), objects=dict(state['objects']))
    reports = {}
    for key, kind in [('dbc', 'dbc'), ('target_fp', 'fp')] + ([('bp', 'bp')] if binding_plan else []):
        item = state['objects'].get(key)
        if item is None:
            reports[key] = dict(status='unsupported', reason='primary document absent')
            continue
        material = materials[item['material_id']]
        text = material['text']
        primary_text=text
        try:
            primary_envelope=json.loads(primary_text)
        except ValueError:
            primary_envelope=None
        if isinstance(primary_envelope,dict) and primary_envelope.get('schema')=='autodriver.semantic-document/v1':
            primary_text=primary_envelope['raw_text']
        compiled=state.get('derived_views',{}).get(key)
        if compiled is not None:
            view_material=materials[compiled]
            view=json.loads(view_material['text'])
            if (view.get('schema')!='autodriver.derived-executable-view/v1' or view.get('object')!=key
                    or view.get('primary_material_id')!=material['id']
                    or view.get('primary_material_sha256')!=material['sha256']
                    or view.get('normative') is not False or material['id'] not in view_material['parents']):
                raise ContractError('derived view does not bind the current readonly primary document')
            text=view.get('content')
            if text is None:
                reports[key]=dict(status='unsupported',reason='readonly view compilation unavailable',
                    primary_material_id=material['id'],primary_material_sha256=material['sha256'],
                    compiler_observation=view.get('checks'),entailment='unverified')
                continue
        # Imported readonly FP is already constructor text. Task-local primary
        # documents are exact host envelopes carrying the captured response.
        try:
            envelope = json.loads(text)
        except ValueError:
            envelope = None
        if isinstance(envelope, dict) and envelope.get('schema') == 'autodriver.semantic-document/v1':
            text = envelope['raw_text']
        content, report = executable_view(text, kind)
        reports[key] = dict(report, primary_material_id=material['id'],
                            primary_material_sha256=material['sha256'],
                            checker_input_sha256=report.get('primary_sha256'),
                            primary_sha256=digest(primary_text.encode()))
        if content is not None:
            identity = 'projection.' + key + '.' + digest(content.encode())[:16]
            projected['materials'].append(dict(id=identity, text=content, sha256=digest(content.encode()),
                                               parents=[material['id']], kind='derived_executable_projection'))
            projected['objects'][key] = dict(item, material_id=identity)
    # A missing BP view does not prevent checks that depend only on DBC/FP.
    # Keep the actual primary BP in the source state; the executable state has
    # no BP rather than inventing an empty binding or claiming its checks ran.
    if binding_plan and reports['bp']['status']!='available':
        projected['objects'].pop('bp',None)
    available = all(reports[key]['status']=='available' for key in ('dbc','target_fp'))
    return projected if available else None, dict(
        schema='autodriver.document-native-projection/v1', status='available' if available else 'not_checked',
        objects=reports, semantic_entailment='unverified',
        unsupported_objects=[key for key,report in reports.items() if report['status']!='available'],
        scope='optional derived views; native execution and independent scoring remain separate')
