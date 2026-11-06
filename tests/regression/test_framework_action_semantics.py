"""Original-source recovery and immutable FP/BP semantics, no model/HW calls."""
import json
from pathlib import Path
import tempfile
import unittest

from _public_workspace_fixture import prepared
from method.autodriver.implementation.system.document_pipeline import DocumentPipeline
from method.autodriver.implementation.system.contract.model import parse
from method.autodriver.implementation.system.evidence import Evidence, SourceIndex
from method.autodriver.implementation.system.framework import select_framework
from method.autodriver.implementation.system.protocol.assembler import canonical, digest
from method.autodriver.implementation.system.workspace import Workspace


HEADER = '''#include <nuttx/registry_types.h>
struct sensor_lowerhalf_s { int type; int (*fetch)(void); };
int sensor_register(struct sensor_lowerhalf_s *object, int number);
'''
TYPES = '''enum public_kind { RESERVED_KIND = 0, VALID_KIND = 1, KIND_COUNT = 2 };
struct public_record { int reading; };
struct public_meta { unsigned span; const char *name; };
'''
PROVIDER = '''#include <nuttx/sensors/sensor.h>
static const struct public_meta formats[] = {
    {0, 0}, {sizeof(struct public_record), "valid"}
};
static int accept(struct sensor_lowerhalf_s *object, unsigned span) {
    if (object->type >= KIND_COUNT || !span) return -7;
    return 0;
}
int sensor_register(struct sensor_lowerhalf_s *object, int number) {
    return accept(object, formats[object->type].span);
}
static const int unrelated_data = 19;
'''


def public_workspace(root):
    prepared(root)
    files = {'target/include/nuttx/sensors/sensor.h': HEADER,
             'target/include/nuttx/registry_types.h': TYPES,
             'target/drivers/sensors/sensor.c': PROVIDER,
             'target/drivers/sensors/unrelated_device.c': 'int device_answer = 91;\n'}
    manifest = json.loads((root/'materials.json').read_text())
    for name, text in files.items():
        p = root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        manifest['files'][name] = dict(sha256=digest(p.read_bytes()), executable=False)
    manifest['identity'] = digest(canonical(manifest['files']).encode())
    (root/'materials.json').write_text(json.dumps(manifest))


class FrameworkActionSemanticsTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        public_workspace(root)
        return root

    def evidence(self, source, *, kind='framework_source', suffix='.c'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root/('input'+suffix)).write_text(source)
        return Evidence(root, {'materials': [dict(id='raw', path='input'+suffix,
            kind=kind, approved=True, parents=[], os_role='target',
            sha256=digest(source.encode()))]})

    def rendered(self, evidence, chunks):
        return ''.join(evidence.excerpt(s['material_id'], s['start_line'],
            s['end_line'], maximum_lines=20000)['text'] for s in chunks['selections'])

    def test_api_closure_includes_whole_referenced_data_and_validity_dependencies(self):
        evidence = self.evidence(TYPES+PROVIDER)
        index = SourceIndex(evidence)
        index.add('raw')
        chunks = index.excerpts(['sensor_register'])
        text = self.rendered(evidence, chunks)
        self.assertIn('{0, 0}, {sizeof(struct public_record), "valid"}', text)
        self.assertIn('object->type >= KIND_COUNT || !span', text)
        self.assertIn('enum public_kind', text)
        self.assertNotIn('unrelated_data', text)
        bounded = index.excerpts(['sensor_register'], maximum_lines=3)
        self.assertTrue(bounded['gaps'])
        self.assertLessEqual(bounded['lines'], 3)

    def test_globals_keep_preprocessor_uncertainty_and_exclude_local_or_device_data(self):
        text = '''#if CONFIG_VARIANT
static const unsigned first = 2, second = 3;
#endif
int invoke(void) { unsigned local = 9; return first + second + local; }
'''
        index = SourceIndex(self.evidence(text))
        index.add('raw')
        self.assertTrue({'first','second'} <= set(index.by_name))
        self.assertNotIn('local', index.by_name)
        self.assertTrue(index.symbols[index.by_name['first'][0]].conditions)
        device = SourceIndex(self.evidence(text, kind='device_source'))
        device.add('raw')
        self.assertNotIn('first', device.by_name)

    def test_trait_closure_recovers_lifecycle_implementations_without_dispatch_claim(self):
        source = '''trait Port { fn activate(&self); fn send(&self); }
impl Port for Adapter {
 fn activate(&self) { self.armed.set(true); }
 fn send(&self) { if self.armed.get() { self.transfer(); } }
}
'''
        evidence = self.evidence(source, suffix='.rs')
        index = SourceIndex(evidence)
        index.add('raw')
        chunks = index.excerpts(['Port'])
        self.assertIn('self.armed.set(true)', self.rendered(evidence, chunks))
        self.assertTrue(any('receiver dispatch not established' in x.get('resolution','')
                            for x in chunks['dependencies']))

    def test_dispatch_table_function_references_keep_provider_conditions(self):
        source = '''struct dispatch { int (*configure)(int); };
#if CONFIG_PROVIDER
int configure_backend(int value) { return value == 3 ? 0 : -1; }
#endif
static const struct dispatch operations = { .configure = configure_backend };
int configure_entry(int value) { return operations.configure(value); }
'''
        evidence = self.evidence(source)
        index = SourceIndex(evidence)
        index.add('raw')
        chunks = index.excerpts(['configure_entry'])
        self.assertIn('return value == 3 ? 0 : -1', self.rendered(evidence, chunks))
        self.assertTrue(index.symbols[index.by_name['configure_backend'][0]].conditions)
        self.assertTrue(any('active dispatch not established' in x.get('resolution','')
                            for x in chunks['dependencies']))

    def test_public_selector_recovers_generic_implementation_data_and_keeps_device_boundary(self):
        workspace = Workspace(self.fixture()).bootstrap()
        ids, scope_id, audit = select_framework(workspace, 'target')
        paths = {workspace.entries[i]['path'] for i in ids}
        self.assertIn('target/drivers/sensors/sensor.c', paths)
        self.assertNotIn('target/drivers/sensors/unrelated_device.c', paths)
        text = self.rendered(workspace.evidence(), audit)
        self.assertIn('{0, 0}, {sizeof(struct public_record), "valid"}', text)
        scope = json.loads(workspace.evidence().read(scope_id))
        self.assertEqual(scope['semantic_recovery_revision'], 'framework-action-establishment-with-abi/v2')
        self.assertTrue(any('!span' in condition['condition']
            for item in scope['mechanical_control_conditions'] for condition in item['conditions']))
        self.assertNotIn('sensor', json.dumps(scope.get('native_abi_configuration', {})))

    def test_stale_shared_fp_is_readonly_seed_and_bp_sees_actual_framework_actions(self):
        root = self.fixture()
        seen = []
        def client(prompt, **kwargs):
            data = json.loads(prompt)
            seen.append(data)
            kind = data['expected_artifact_kind']
            if data['call'].get('view_only') and kind=='fp':
                return "FrameworkProfile('nuttx',Ref('scope'))"
            if kind == 'code':
                return json.dumps(dict(kind='code', format='file_edits', edits=[dict(
                    path='target/drivers/sensors/synthetic.c', operation='create',
                    base_sha256=None, content='int synthetic_register(void) {return 0;}\n')]))
            if kind == 'fp':
                self.assertTrue(all(x['os_role'] in {'shared','target'} for x in data['context']))
                text = ''.join(x['text'] for x in data['context'] if x['role']=='framework_evidence')
                self.assertIn('formats[object->type].span', text)
                self.assertIn('{0, 0}', text)
                self.assertFalse(any(x['kind'] in {'public_task_projection','device_source'}
                                     for x in data['context']))
                return 'Native registration requires a valid metadata entry with nonzero event span; default zero is reserved.'
            if kind == 'bp' and not data['call'].get('view_only'):
                self.assertTrue(any(x['role']=='framework_evidence' and 'formats' in x['text']
                                    for x in data['context']))
                self.assertIn('nonzero event span', ''.join(x['text'] for x in data['context']
                                                          if x['role']=='target_fp'))
                return 'The driver initializes the valid selector before registration; success establishes ready, failure leaves it unregistered.'
            return 'Readable draft with explicit gaps; no executable view is available.'
        pipeline = DocumentPipeline(root, client=client, variant_name='without_source_framework_lifting')
        pipeline.select()
        old_scope = json.loads(pipeline.workspace.evidence().read(pipeline.framework_scope['target']))
        old_scope.pop('semantic_recovery_revision')
        old_id = pipeline.workspace.add_derived('old.scope','old-scope.json','framework_scope',
            'framework_context',canonical(old_scope)+'\n',pipeline.framework_ids['target'],os_role='target')
        old_text = "FrameworkProfile('nuttx', Ref('old.scope'))\n"
        old_fp = pipeline.workspace.add_derived('old.fp','old-fp.txt','fp_shared','target_fp',
            old_text,[old_id],os_role='target')
        pipeline.saved_objects['target_fp'] = dict(seed_only=False,
            root=parse(old_text,'fp'), artifact=dict(kind='fp',format='constructor',content=old_text),
            material_id=old_fp,checks=dict(status='unknown'),origin='synthetic frozen profile',framework_version='old')
        before = pipeline.workspace.entries[old_fp]['sha256']
        pipeline.dbc()
        pipeline.fp('target')
        self.assertTrue(any(x['call']['stage']=='fp_build' for x in seen))
        self.assertEqual(pipeline.workspace.entries[old_fp]['sha256'], before)
        self.assertEqual(pipeline.workspace.evidence().read(old_fp).decode(), old_text)
        self.assertFalse(pipeline.document_imports)
        pipeline.bp()
        self.assertEqual(pipeline.propose_document_code()['status'], 'candidate_proposed')
        code = next(x for x in seen if x['expected_artifact_kind']=='code')
        self.assertEqual(code['generation_policy'], 'advisory_contract_checks')
        self.assertEqual(pipeline.code_proposal['full_scope_gate'], 'not_required_advisory')
        self.assertTrue(any(x['role']=='bp' for x in code['context']))


if __name__ == '__main__':
    unittest.main()
