"""Readonly view compilation is not contract recovery or code admission."""
import json
from pathlib import Path
import tempfile
import unittest

from _public_workspace_fixture import prepared
from method.autodriver.implementation.system.document_pipeline import DocumentPipeline
from method.autodriver.implementation.system.document_view_compiler import compile_views
from method.autodriver.implementation.system.document_projection import project_native_state
from method.autodriver.implementation.system.protocol.assembler import MethodLimitError
from method.autodriver.implementation.system.contract.model import ContractError
from tests.common.autodriver_document_state import load_document_state

DBC="DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))"
BP="BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),{})"


class DocumentViewCompilerTests(unittest.TestCase):
    def fixture(self,variant='full',bad_view=False,readable_fp=False):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        root=Path(folder.name);prepared(root)
        seen=[]
        def client(prompt,**kwargs):
            data=json.loads(prompt);seen.append(data)
            if data['call'].get('view_only'):
                self.assertEqual(data['tool_interfaces'],{})
                self.assertFalse(any(c['role'] in {'source_evidence','document_evidence','internal_diagnostic','candidate_code'} for c in data['context']))
                if data['call']['repair_target']=='fp':return "FrameworkProfile('synthetic',Ref('scope'))"
                return 'unsupported executable view' if bad_view is True or bad_view==data['call']['repair_target'] else DBC if data['call']['repair_target']=='dbc' else BP
            if data['expected_artifact_kind']=='fp':
                return 'Readable readonly framework profile with explicit gaps.' if readable_fp else "FrameworkProfile('synthetic',Ref('scope'))"
            if data['expected_artifact_kind']=='code':
                return json.dumps(dict(kind='code',format='file_edits',edits=[dict(
                    path='target/drivers/sensors/synthetic.c',operation='create',base_sha256=None,
                    content='int synthetic_register(void) { return 0; }\n')]))
            return 'Readonly primary '+data['expected_artifact_kind']+' with explicit semantic gaps.'
        return DocumentPipeline(root,client=client,variant_name=variant),seen

    def test_readable_target_fp_gets_a_framework_only_readonly_view(self):
        pipeline,seen=self.fixture(readable_fp=True)
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        calls=[d for d in seen if d['call'].get('view_only') and d['call']['repair_target']=='fp']
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['call']['condition'],'FRAMEWORK')
        self.assertEqual(calls[0]['call']['framework_role'],'target')
        self.assertEqual({c['role'] for c in calls[0]['context']},{'method_specification','candidate'})
        self.assertEqual(pipeline.documents['target_fp'].raw_text,'Readable readonly framework profile with explicit gaps.')
        self.assertIn('target_fp',pipeline.derived_views)

    def test_primary_documents_and_code_revision_are_not_repaired_by_view_compiler(self):
        pipeline,seen=self.fixture()
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertEqual(set(pipeline.derived_views),{'dbc','bp'})
        primary={k:(v.identity,v.raw_text) for k,v in pipeline.documents.items()}
        revisions=dict(pipeline.revisions.current)
        calls=len(pipeline.runner.calls)
        compile_views(pipeline)
        self.assertEqual(primary,{k:(v.identity,v.raw_text) for k,v in pipeline.documents.items()})
        self.assertEqual(revisions,pipeline.revisions.current)
        self.assertEqual(calls,len(pipeline.runner.calls))
        self.assertEqual(pipeline.code_repairs_used(),0)
        self.assertEqual([d['call']['stage'] for d in seen][-3:],['code_generate','repair','repair'])

    def test_without_refinement_only_first_primary_dbc_is_recovered(self):
        pipeline,seen=self.fixture('without_dbc_evidence_refinement')
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        ordinary=[d for d in seen if d['expected_artifact_kind']=='dbc' and not d['call'].get('view_only')]
        views=[d for d in seen if d['call'].get('view_only') and d['call']['repair_target']=='dbc']
        self.assertEqual(len(ordinary),1)
        self.assertEqual(len(views),1)
        self.assertEqual(pipeline.documents['dbc'].raw_text,'Readonly primary dbc with explicit semantic gaps.')

    def test_without_binding_has_neither_primary_bp_nor_bp_view(self):
        pipeline,seen=self.fixture('without_binding_plan')
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertNotIn('bp',pipeline.documents)
        self.assertNotIn('bp',pipeline.derived_views)
        self.assertFalse(any(d['expected_artifact_kind']=='bp' for d in seen))

    def test_unsupported_views_never_discard_or_repair_the_existing_code(self):
        pipeline,seen=self.fixture(bad_view=True)
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertIsNotNone(pipeline.code_proposal)
        self.assertEqual(pipeline.code_repairs_used(),0)
        self.assertEqual(sum(d['call'].get('view_only',False) for d in seen),2)
        for material in pipeline.derived_views.values():
            value=json.loads(pipeline.workspace.evidence().read(material))
            self.assertIsNone(value['content'])
            self.assertFalse(value['normative'])

    def test_view_context_limit_preserves_existing_candidate_and_records_unknown(self):
        pipeline,seen=self.fixture()
        pipeline.max_context_bytes=524288
        original=pipeline.runner.run
        def bounded(request,**kwargs):
            if request.get('view_only'):
                raise MethodLimitError('logical_calls',64,64)
            return original(request,**kwargs)
        pipeline.runner.run=bounded
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        for material in pipeline.derived_views.values():
            value=json.loads(pipeline.workspace.evidence().read(material))
            self.assertEqual(value['checks']['status'],'not_checked')
        self.assertIsNotNone(pipeline.code_proposal)

    def test_compile_only_receives_the_same_upstream_readonly_views(self):
        pipeline,seen=self.fixture('compile_only_repair')
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        self.assertEqual(set(pipeline.derived_views),{'dbc','bp'})
        self.assertEqual(pipeline.code_repairs_used(),0)

    def state(self,pipeline):
        state=json.loads((pipeline.output/'contract-state.json').read_text())
        return load_document_state(pipeline.workspace.root,state,pipeline.code_proposal,pipeline.workspace.task,
                                   pipeline.workspace.manifest,variant_name=pipeline.variant_name)

    def test_compiled_views_reach_the_native_state_with_current_primary_bindings(self):
        pipeline,_=self.fixture()
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        state=self.state(pipeline)
        projected,report=project_native_state(state,binding_plan=True)
        self.assertIsNotNone(projected)
        self.assertEqual(report['status'],'available')
        self.assertEqual(report['semantic_entailment'],'unverified')
        materials={m['id']:m for m in projected['materials']}
        self.assertEqual(materials[projected['objects']['dbc']['material_id']]['text'],DBC)
        self.assertEqual(state['objects']['dbc']['material_id'],pipeline.material_ids['dbc'])

    def test_missing_bp_view_preserves_dbc_fp_checks_without_fabricating_a_bp(self):
        pipeline,_=self.fixture(bad_view='bp')
        pipeline.run()
        state=self.state(pipeline)
        projected,report=project_native_state(state,binding_plan=True)
        self.assertIsNotNone(projected)
        self.assertNotIn('bp',projected['objects'])
        self.assertIn('bp',state['objects'])
        self.assertEqual(report['unsupported_objects'],['bp'])

    def test_stale_or_misattributed_view_cannot_run_against_a_new_primary(self):
        pipeline,_=self.fixture()
        pipeline.run();state=self.state(pipeline)
        material=next(m for m in state['materials'] if m['id']==state['derived_views']['dbc'])
        view=json.loads(material['text']);view['primary_material_sha256']='0'*64
        material['text']=json.dumps(view)
        with self.assertRaisesRegex(ContractError,'current readonly primary'):
            project_native_state(state,binding_plan=True)


if __name__=='__main__':
    unittest.main()
