"""Host validation of prepared paths and exact semantic-document identities.

No contract-language admission is performed. This module has no scorer,
reference or evaluation-feedback interface.
"""
import json

from method.autodriver.implementation.system.protocol.assembler import PROTOCOLS, canonical, read_pinned
from method.autodriver.implementation.system.semantic_documents import SemanticDocument, ParentIdentity
from method.autodriver.implementation.system.document_code import extract_code


def load_document_state(agent, state, proposal, task, manifest, *, variant_name):
    if (state.get('schema') != 'autodriver.document-contract-state/v1'
            or state.get('task_id') != task['task_id']
            or state.get('input_identity') != manifest['identity']
            or state.get('variant') != variant_name
            or state.get('generation_policy') != 'advisory_contract_checks'
            or proposal.get('generation_policy') != 'advisory_contract_checks'
            or proposal.get('full_scope_gate') != 'not_required_advisory'):
        raise ValueError('document task/input/variant/policy identity mismatch')
    entries = state['materials']
    if not isinstance(entries, list) or len(entries) > 4096:
        raise ValueError('bounded document material inventory required')
    materials = {entry['id']: dict(entry) for entry in entries}
    if len(materials) != len(entries):
        raise ValueError('duplicate document material IDs')
    allowed = set().union(*map(set, PROTOCOLS['role_kinds'].values()))
    verified, visiting = {}, set()

    def verify(identity):
        if identity in visiting:
            raise ValueError('cyclic document material ancestry')
        if identity in verified:
            return
        entry = materials[identity]
        if entry['kind'] not in allowed or entry.get('approved') is not True:
            raise ValueError('unapproved document material')
        path = entry['path']
        if path in manifest['files']:
            if entry['sha256'] != manifest['files'][path]['sha256']:
                raise ValueError('prepared original hash changed')
        elif not path.startswith('output/autodriver/'):
            raise ValueError('derived document must be inside prepared method output')
        if entry['kind'] in PROTOCOLS['derived_kinds'] and not entry['parents']:
            raise ValueError('derived document has no registered parents')
        if entry['kind'] == 'internal_diagnostic' and entry.get('producer') not in PROTOCOLS['diagnostic_producers']:
            raise ValueError('unapproved document diagnostic producer')
        visiting.add(identity)
        for parent in entry['parents']:
            verify(parent)
        data = read_pinned(agent, entry)
        if entry['kind']=='device_document' and path.lower().endswith('.pdf'):
            verified[identity] = dict(entry, encoding='binary', size_bytes=len(data))
        else:
            verified[identity] = dict(entry, text=data.decode('utf8'))
        visiting.remove(identity)

    for identity in materials:
        verify(identity)
    objects = state['objects']
    code = objects['code']
    if code['revision'] != proposal['revision'] or json.loads(verified[code['material_id']]['text']) != proposal['artifact']:
        raise ValueError('document code proposal differs from current material/revision')
    for key, record in state['documents'].items():
        document = SemanticDocument(record['kind'], record['task_id'], record['call_id'], record['stage'],
            record['raw_text'], tuple(ParentIdentity(**p) for p in record['parents']),
            record['os_role'], record['response_role'])
        if document.record() != record or document.task_id != task['task_id']:
            raise ValueError('semantic document identity changed')
        expected = document.os_role + '_fp' if document.kind == 'fp' else document.kind
        if key != expected:
            raise ValueError('semantic document object role changed')
        material = verified[objects[key]['material_id']]
        if material['text'] != document.raw_text and json.loads(material['text']) != record:
            raise ValueError('primary semantic document differs from material')
        for parent in document.parents:
            if verified[parent.material_id]['sha256'] != parent.sha256:
                raise ValueError('semantic document parent changed')
    request = proposal['request']
    if request['editable_paths'] != ['target/' + p for p in task['submission']['editable_paths']]:
        raise ValueError('document code request widens public editable paths')
    for path, value in request.get('base_files', {}).items():
        if manifest['files'].get(path, {}).get('sha256') != value:
            raise ValueError('document code baseline differs from prepared input')
    artifact, _ = extract_code(canonical(proposal['artifact']), request)
    for edit in artifact['edits']:
        original = manifest['files'].get(edit['path'])
        if (original is None) != (edit['operation'] == 'create'):
            raise ValueError('document edit create/replace differs from actual prepared baseline')
    return dict(state, materials=list(verified.values()))
