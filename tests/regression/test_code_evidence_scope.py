"""Real finite retrieval and ABI closure, synthetic originals, no model/network."""
import json
from pathlib import Path
import tempfile
import unittest

from test_framework_action_semantics import public_workspace
from method.autodriver.implementation.system.document_calls import DocumentCalls
from method.autodriver.implementation.system.document_pipeline import DocumentPipeline
from method.autodriver.implementation.system.evidence import Evidence, SourceIndex
from method.autodriver.implementation.system.protocol.assembler import digest


class CodeEvidenceScopeTests(unittest.TestCase):
    def command(self, outer, inner='target_framework', query=None):
        return json.dumps(dict(context_requests=[dict(scope=outer, question='Read exact ABI', reason='Need a declared type',
            query=query if query is not None else json.dumps(dict(tool='inspect_symbol',
                arguments=dict(scope=inner,symbol='PublicInterface'))))]))

    def parse(self, raw):
        return DocumentCalls.query_requests(object.__new__(DocumentCalls),raw)

    def test_advertised_original_scope_executes_the_same_finite_command_as_internal_tools(self):
        _,commands,refusal=self.parse(self.command('target_framework'))
        _,legacy,legacy_refusal=self.parse(self.command('internal_tools'))
        self.assertIsNone(refusal);self.assertIsNone(legacy_refusal)
        self.assertEqual(commands,legacy)
        self.assertEqual(commands[0]['arguments']['scope'],'target_framework')

    def test_outer_scope_cannot_disguise_other_source_authority(self):
        _,commands,refusal=self.parse(self.command('source_framework'))
        self.assertFalse(commands);self.assertIsNotNone(refusal)

    def test_free_text_or_extra_command_fields_are_not_executed(self):
        for query in ['read the target header',json.dumps(dict(tool='inspect_symbol',arguments={},path='/private'))]:
            with self.subTest(query=query):
                _,commands,refusal=self.parse(self.command('target_framework',query=query))
                self.assertFalse(commands);self.assertIsNotNone(refusal)

    def test_abi_type_closure_preserves_callback_and_error_declarations_without_provider_bodies(self):
        source='''pub enum PublicError { Failed }
pub trait EventSink { fn finished(&self, value: Result<i32, PublicError>); }
pub trait PublicInterface<'a> { fn set_client(&self, sink: &'a dyn EventSink); }
pub trait UnrelatedSink { fn unrelated(&self); }
impl PublicInterface<'static> for OtherProvider {
 fn set_client(&self, sink: &'static dyn EventSink) { unwanted_provider_algorithm(); }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'raw.rs').write_text(source)
            evidence=Evidence(root,dict(materials=[dict(id='raw',path='raw.rs',kind='framework_source',
                approved=True,parents=[],os_role='target',sha256=digest(source.encode()))]))
            index=SourceIndex(evidence);index.add('raw')
            result=index.excerpts(['PublicInterface'],declarations_only=True)
            text=''.join(evidence.excerpt(x['material_id'],x['start_line'],x['end_line'],maximum_lines=2000)['text'] for x in result['selections'])
            self.assertIn('fn finished(&self, value: Result<i32, PublicError>)',text)
            self.assertIn('pub enum PublicError',text)
            self.assertNotIn('unwanted_provider_algorithm',text)
            self.assertNotIn('trait UnrelatedSink',text)
            self.assertEqual(result['stop'],'bounded_declaration_type_closure')

    def test_real_scope_aligned_retrieval_then_new_file_proposal_uses_same_repair_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);public_workspace(root);code_calls=[]
            def client(prompt,**kwargs):
                data=json.loads(prompt);call=data['call']
                if data['expected_artifact_kind']!='code':return 'Readable draft; actual provider/progress guarantees remain unknown.'
                code_calls.append(data)
                boundary=data['implementation_boundary']
                self.assertIn('target/drivers/sensors/synthetic.c',boundary['missing_exact_editable_files'])
                self.assertEqual(boundary['existing_editable_files'],{})
                self.assertTrue(any(x['role']=='framework_evidence' and 'struct sensor_lowerhalf_s' in x['text'] for x in data['context']))
                scopes=data['response_schema']['properties']['context_requests']['items']['properties']['scope']['enum']
                self.assertIn('target_framework',scopes);self.assertNotIn('upstream_stage',scopes)
                if len(code_calls)==1:
                    return json.dumps(dict(context_requests=[dict(scope='target_framework',question='Read original fields',
                        reason='Native registration consumes this ABI',query=json.dumps(dict(tool='inspect_symbol',
                            arguments=dict(scope='target_framework',symbol='sensor_lowerhalf_s'))))],artifact=None))
                return json.dumps(dict(kind='code',format='file_edits',edits=[dict(
                    path='target/drivers/sensors/synthetic.c',operation='create',base_sha256=None,
                    content='int synthetic_register(void) { return 0; }\n')]))
            pipeline=DocumentPipeline(root,client=client,variant_name='without_source_framework_lifting')
            result=pipeline.run()
            self.assertEqual(result['status'],'candidate_proposed')
            self.assertEqual(len(code_calls),2)
            self.assertEqual(pipeline.code_repairs_used(),1)
            self.assertTrue(any(e['query']['tool']=='inspect_symbol' and e['status']!='refused' for e in pipeline.runner.tool_events))
            self.assertFalse((root/'target/drivers/sensors/synthetic.c').exists())
            current=pipeline.workspace.entries[pipeline.material_ids['code']]
            self.assertTrue(any(pipeline.workspace.entries[x]['kind']=='framework_source' for x in current['parents']))

    def test_abi_projection_recovers_integer_selector_enum_and_metadata_without_api_body(self):
        source='''enum selector_kind { RESERVED_SELECTOR = 0, VALID_SELECTOR = 1, SELECTOR_COUNT = 2 };
struct registry_entry { int selector; };
struct registry_meta { unsigned span; };
static const struct registry_meta descriptions[] = {
 [RESERVED_SELECTOR] = {0}, [VALID_SELECTOR] = {8}
};
static int accept_entry(struct registry_entry *entry) {
 if (entry->selector >= SELECTOR_COUNT || !descriptions[entry->selector].span) return -1;
 return 0;
}
int register_entry(struct registry_entry *entry) { return accept_entry(entry); }
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'raw.c').write_text(source)
            evidence=Evidence(root,dict(materials=[dict(id='raw',path='raw.c',kind='framework_source',
                approved=True,parents=[],os_role='target',sha256=digest(source.encode()))]))
            index=SourceIndex(evidence);index.add('raw')
            result=index.excerpts(['register_entry'],declarations_only=True)
            text=''.join(evidence.excerpt(x['material_id'],x['start_line'],x['end_line'],maximum_lines=2000)['text'] for x in result['selections'])
            self.assertIn('enum selector_kind',text)
            self.assertIn('[RESERVED_SELECTOR] = {0}, [VALID_SELECTOR] = {8}',text)
            self.assertIn('struct registry_entry',text)
            self.assertNotIn('return accept_entry(entry)',text)
            self.assertNotIn('return -1',text)


if __name__=='__main__':unittest.main()
