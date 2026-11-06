"""Actual host input validation, fake public compiler; no paid calls."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from benchmark.common.api import ROOT, save, digest
from _public_workspace_fixture import prepared
from method.autodriver.implementation.system.document_pipeline import DocumentPipeline
from tests.common.autodriver_tools import CandidateTools
from tests.common.autodriver_document_state import load_document_state
from method.autodriver.implementation.system.contract.model import parse


def synthetic_client(prompt, **kwargs):
    call=json.loads(prompt)
    if call['expected_artifact_kind']=='code':
        return json.dumps(dict(kind='code',format='file_edits',edits=[dict(
            path='target/drivers/sensors/synthetic.c',operation='create',base_sha256=None,
            content='int synthetic_register(void) { return 0; }\n')]))
    return 'Readable draft with explicit unknowns; no constructor.'


class DocumentHostTests(unittest.TestCase):
    def fixture(self, policy='advisory_contract_checks', variant='full'):
        temporary=tempfile.TemporaryDirectory(dir=ROOT/'run')
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        agent=root/'agent';agent.mkdir()
        prepared(agent)
        pipeline=DocumentPipeline(agent, client=synthetic_client, variant_name=variant)
        self.assertEqual(pipeline.run()['status'],'candidate_proposed')
        baseline=root/'host/baseline';baseline.mkdir(parents=True)
        service=CandidateTools(agent,baseline,root/'host/tools',variant_name=variant,
                               deadline=time.monotonic()+60,admission_policy=policy)
        return pipeline,service

    def request(self,pipeline):
        return dict(schema='autodriver.candidate-check-request/v1',revision=pipeline.code_proposal['revision'],
            proposal_sha256=digest(pipeline.output/'code-proposal.json'),state_sha256=digest(pipeline.output/'contract-state.json'))

    def fake_prepare(self,agent,baseline,candidate,proposal):
        (candidate/'target').mkdir(parents=True)
        (candidate/'target/driver.c').write_text('synthetic candidate\n')
        (candidate/'output/submission').mkdir(parents=True)
        (candidate/'output/submission/driver.patch').write_text('synthetic patch\n')
        save(candidate/'output/submission/manifest.json',{'scope':'offline fixture'})
        record=dict(revision=proposal['revision'],changes={'target/driver.c':digest(candidate/'target/driver.c')})
        save(candidate/'candidate.json',record)
        return record

    def test_explicit_host_policy_accepts_readable_drafts_and_can_freeze_applied_source(self):
        pipeline,service=self.fixture()
        with patch('tests.common.autodriver_tools.prepare_candidate',side_effect=self.fake_prepare), \
             patch('tests.common.autodriver_tools.public_compile',return_value=dict(status='pass',commands=[],execution={'cleanup':{'status':'pass'}})), \
             patch('tests.common.autodriver_tools.native_nuttx') as native:
            native.return_value=dict(status='observed',native_candidate_executed=True,
                checks=dict(status='unknown'),execution=dict(cleanup=dict(status='pass')))
            result=service.check(self.request(pipeline))
        native.assert_called_once()
        self.assertEqual(result['native_execution_status'],'observed')
        self.assertEqual(result['applied_revision'],pipeline.code_proposal['revision'])
        self.assertEqual(service.freeze_final(service.directory/'freeze.json')['candidate_revision'],result['revision'])

    def test_model_document_flag_cannot_enable_advisory_policy_on_legacy_host(self):
        pipeline,service=self.fixture(policy='strict_contract_checks')
        with patch('tests.common.autodriver_tools.prepare_candidate') as prepare:
            with self.assertRaisesRegex(ValueError,'legacy candidate state'):
                service.check(self.request(pipeline))
        prepare.assert_not_called()

    def test_hash_path_role_and_public_edit_boundaries_stay_strict(self):
        for change in ('original_drift','derived_link','widen_path','document_role','material_parent'):
            with self.subTest(change=change):
                pipeline,service=self.fixture()
                state=json.loads((pipeline.output/'contract-state.json').read_text())
                proposal=copy.deepcopy(pipeline.code_proposal)
                if change=='original_drift':
                    path=next(e['path'] for e in state['materials'] if e['kind']=='device_source')
                    (pipeline.workspace.root/path).write_text('input drift\n')
                elif change=='derived_link':
                    entry=next(e for e in state['materials'] if e['kind']=='dbc_candidate')
                    target=pipeline.workspace.root/entry['path'];target.unlink();target.symlink_to(ROOT/'README.md')
                elif change=='widen_path':
                    proposal['request']['editable_paths']=['target/**']
                    save(pipeline.output/'code-proposal.json',proposal)
                elif change=='document_role':
                    state['documents']['target_fp']['os_role']='source'
                else:
                    state['materials'][-1]['parents']=['nonexistent-parent']
                save(pipeline.output/'contract-state.json',state)
                with patch('tests.common.autodriver_tools.prepare_candidate') as prepare:
                    with self.assertRaises((ValueError,KeyError)):
                        service.check(self.request(pipeline))
                prepare.assert_not_called()

    def test_compile_only_receives_no_candidate_contract_status_or_diagnostic(self):
        pipeline,service=self.fixture(variant='compile_only_repair')
        with patch('tests.common.autodriver_tools.prepare_candidate',side_effect=self.fake_prepare), \
             patch('tests.common.autodriver_tools.public_compile',return_value=dict(status='pass',commands=[],execution={'cleanup':{'status':'pass'}})), \
             patch('tests.common.autodriver_tools.native_nuttx',return_value=dict(status='observed',native_candidate_executed=True,checks=dict(status='violated'),execution=dict(cleanup=dict(status='pass')))):
            result=service.check(self.request(pipeline))
        self.assertNotIn('native_checks_status',result)
        self.assertNotIn('native_execution_status',result)
        self.assertNotIn('upstream_diagnostics',result)
        self.assertEqual(result['repair_diagnostics'],[])

    def test_frozen_profile_import_preserves_exact_material_bytes_including_final_newline(self):
        temporary=tempfile.TemporaryDirectory(dir=ROOT/'run');self.addCleanup(temporary.cleanup)
        agent=Path(temporary.name)/'agent';agent.mkdir();prepared(agent)
        pipeline=DocumentPipeline(agent,client=synthetic_client)
        pipeline.select()
        text="FrameworkProfile('synthetic',Ref('scope'))"
        material='synthetic.frozen-source-profile'
        pipeline.workspace.add_derived(material,'synthetic-fp.txt','fp_task_local','existing_fp',
            text+'\n',[pipeline.framework_scope['source']],os_role='source')
        pipeline.saved_objects['source_fp']=dict(root=parse(text,'fp'),
            artifact=dict(kind='fp',format='constructor',content=text),material_id=material,
            checks={'status':'unknown'},origin={'request_id':'synthetic-frozen-import'})
        with patch.object(pipeline,'fp_scope_current',return_value=True):
            pipeline.fp('source')
        pipeline.dbc();pipeline.fp('target');pipeline.bp();pipeline.propose_document_code()
        self.assertEqual(pipeline.documents['source_fp'].raw_text,text+'\n')
        state=json.loads((pipeline.output/'contract-state.json').read_text())
        result=load_document_state(agent,state,pipeline.code_proposal,pipeline.workspace.task,
                                   pipeline.workspace.manifest,variant_name='full')
        self.assertEqual(result['documents']['source_fp']['raw_text'],text+'\n')


if __name__=='__main__':
    unittest.main()
