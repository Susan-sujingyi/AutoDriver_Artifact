import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from _public_workspace_fixture import prepared
from method.autodriver.implementation.system.document_pipeline import DocumentPipeline
from method.autodriver.implementation.system.protocol.assembler import expected_kind, ContextLimitError


class DocumentPipelineTests(unittest.TestCase):
    def fixture(self,variant='full',bad_code=False):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root=Path(folder.name)
        prepared(root)
        seen=[]
        def client(prompt,**kwargs):
            data=json.loads(prompt)
            seen.append(data)
            call=data['call']
            if data['expected_artifact_kind']=='code':
                if bad_code and call['stage']=='code_generate':
                    return '{unfinished code response'
                return json.dumps(dict(kind='code',format='file_edits',edits=[dict(
                    path='target/drivers/sensors/synthetic.c',operation='create',base_sha256=None,
                    content='int synthetic_register(void) { return 0; }\n')]))
            if data['expected_artifact_kind']=='dbc':
                return 'DeviceContract(unbalanced(\nA readable device draft; timing and progress remain unknown.'
            return 'Readable '+data['expected_artifact_kind']+' draft with explicit provider/resource gaps.'
        return DocumentPipeline(root,client=client,variant_name=variant),seen

    def test_complete_fake_gateway_path_reaches_code_with_unparsed_dbc(self):
        pipeline,seen=self.fixture()
        result=pipeline.run()
        self.assertEqual(result['status'],'candidate_proposed')
        self.assertEqual([d['call']['stage'] for d in seen],
                         ['fp_build','dbc_recover','repair','fp_build','bp_build','code_generate','repair','repair','repair'])
        self.assertTrue(all(d['call'].get('view_only') for d in seen[-2:]))
        self.assertIsNotNone(pipeline.code_proposal)
        self.assertEqual(pipeline.code_proposal['full_scope_gate'],'not_required_advisory')
        self.assertEqual(pipeline.code_repairs_used(),0)
        self.assertFalse((pipeline.workspace.root/'target/drivers/sensors/synthetic.c').exists())
        state=json.loads((pipeline.output/'contract-state.json').read_text())
        self.assertEqual(state['schema'],'autodriver.document-contract-state/v1')
        self.assertEqual(state['native_projection_status'],'not_checked')
        self.assertTrue({'dbc','bp','code'}<=state['objects'].keys())
        self.assertTrue(all('path' in m and 'sha256' in m for m in state['materials']))

    def test_without_source_lifting_has_same_raw_api_but_no_source_fp(self):
        pipeline,seen=self.fixture('without_source_framework_lifting')
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertNotIn('source_fp',pipeline.documents)
        self.assertFalse(any(d['call'].get('framework_role')=='source' for d in seen))
        dbc=next(d for d in seen if d['call']['stage']=='dbc_recover')
        self.assertTrue(any('int transfer(void);' in c['text'] for c in dbc['context']))
        self.assertFalse(any(c['role']=='source_fp' for d in seen for c in d['context']))

    def test_without_refinement_does_not_sneak_in_dbc_repair(self):
        pipeline,seen=self.fixture('without_dbc_evidence_refinement')
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertFalse(any(d['call'].get('repair_target')=='dbc' and not d['call'].get('view_only') for d in seen))
        self.assertEqual(sum(d['expected_artifact_kind']=='dbc' and not d['call'].get('view_only') for d in seen),1)

    def test_without_binding_has_no_bp_call_object_or_placeholder(self):
        pipeline,seen=self.fixture('without_binding_plan')
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertNotIn('bp',pipeline.documents)
        self.assertFalse(any(d['expected_artifact_kind']=='bp' for d in seen))
        code=next(d for d in reversed(seen) if d['expected_artifact_kind']=='code')
        self.assertEqual(code['call']['generation_mode'],'without_binding')
        self.assertFalse(any(c['role']=='bp' for c in code['context']))
        self.assertNotIn('bp',pipeline.checks['generation_policy']['expected_contracts'])

    def test_invalid_code_repair_consumes_shared_code_bound_and_sees_rejected_text(self):
        pipeline,seen=self.fixture(bad_code=True)
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertEqual(pipeline.code_repairs_used(),1)
        code=next(d for d in reversed(seen) if d['expected_artifact_kind']=='code')
        self.assertEqual(code['call']['repair_target'],'code')
        self.assertTrue(any('unfinished code response' in c['text'] for c in code['context']
                            if c['role']=='internal_diagnostic'))

    def test_contract_context_failure_is_recorded_and_does_not_suppress_code(self):
        pipeline,seen=self.fixture()
        original=pipeline.runner.run
        def bounded(request,**kwargs):
            if expected_kind(request)=='dbc':
                raise ContextLimitError(600000,524288)
            return original(request,**kwargs)
        with patch.object(pipeline.runner,'run',side_effect=bounded):
            self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertEqual(pipeline.documents['dbc'].availability,'empty_response')
        self.assertFalse(pipeline.checks['dbc']['local_context_failure']['model_request_issued'])
        self.assertIn('dbc',pipeline.checks['generation_policy']['missing_contracts'])
        self.assertTrue(any(d['call']['stage']=='code_generate' for d in seen))


if __name__=='__main__':
    unittest.main()
