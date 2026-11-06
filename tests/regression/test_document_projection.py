"""Synthetic optional views; no device-specific or benchmark-private answers."""
import json
from pathlib import Path
import tempfile
import unittest

from method.autodriver.implementation.system.document_projection import executable_view, project_native_state
from tests.common.autodriver_document_native import document_native
from method.autodriver.implementation.system.contract.model import ContractError


DBC = "DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))"
FP = "FrameworkProfile('synthetic',Ref('snapshot'))"
BP = "BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),{})"


class DocumentProjectionTests(unittest.TestCase):
    def test_readable_primary_and_optional_projection_keep_unknowns(self):
        raw = 'Primary: progress is conditional.\n```autodriver-projection\n'+DBC+'\n```'
        content, report = executable_view(raw, 'dbc')
        self.assertEqual(content, DBC)
        self.assertEqual(report['status'], 'available')
        self.assertEqual(report['checks']['status'], 'unknown')
        self.assertEqual(report['entailment'], 'unverified')

    def test_parse_failure_and_multiple_views_do_not_choose_a_convenient_view(self):
        for raw in ('Readable only; no executable view',
                    '```autodriver-projection\n'+DBC+'\n```\n```autodriver-projection\n'+DBC+'\n```'):
            content, report = executable_view(raw, 'dbc')
            self.assertIsNone(content)
            self.assertEqual(report['status'], 'unsupported')

    def state(self, include_bp=True):
        entries = [('dbc', DBC), ('target_fp', FP)] + ([('bp', BP)] if include_bp else [])
        return dict(task_id='synthetic', objects={k:dict(material_id=k) for k,_ in entries},
            materials=[dict(id=k,text=json.dumps(dict(schema='autodriver.semantic-document/v1',
                raw_text=json.dumps(dict(document='Readable primary', executable_projection=t)))),sha256='1'*64)
                       for k,t in entries])

    def test_without_binding_does_not_insert_empty_bp(self):
        projected, report = project_native_state(self.state(False), binding_plan=False)
        self.assertEqual(report['status'], 'available')
        self.assertNotIn('bp', projected['objects'])
        self.assertEqual(set(report['objects']), {'dbc','target_fp'})

    def test_available_view_reaches_actual_executor_interface_with_primary_hashes(self):
        seen=[]
        def executor(candidate,destination,state,**kwargs):
            destination.mkdir()
            seen.append(state)
            return dict(status='observed', native_candidate_executed=True, checks={'status':'violated'})
        with tempfile.TemporaryDirectory() as folder:
            result=document_native(executor,Path(folder),Path(folder)/'native',self.state(),
                                   deadline=1,binding_plan=True)
        self.assertEqual(result['status'],'observed')
        self.assertEqual(result['checks']['status'],'violated')
        self.assertEqual(result['primary_document_entailment'],'unverified')
        self.assertEqual(result['projection']['objects']['dbc']['primary_material_sha256'],'1'*64)
        self.assertEqual(len(seen),1)

    def test_absent_view_is_not_checked_and_never_invokes_executor(self):
        state=self.state()
        state['materials'][0]['text']='Unreadable for an executable checker'
        def executor(*args,**kwargs):
            self.fail('an absent view cannot be native execution')
        with tempfile.TemporaryDirectory() as folder:
            result=document_native(executor,Path(folder),Path(folder)/'native',state,
                                   deadline=1,binding_plan=True)
        self.assertEqual(result['status'],'not_checked')
        self.assertFalse(result['native_candidate_executed'])

    def test_deterministic_violation_is_preserved_without_becoming_admission(self):
        content, report=executable_view(DBC.replace('I2C(17)','I2C(True)'), 'dbc')
        self.assertIsNotNone(content)
        self.assertEqual(report['status'],'available')
        self.assertEqual(report['checks']['status'],'violated')

    def test_checker_error_after_execution_retains_actual_execution_and_cleanup(self):
        def executor(candidate,destination,state,**kwargs):
            (destination/'run-process').mkdir(parents=True)
            (destination/'run-process/process.json').write_text(json.dumps(
                dict(status='pass',cleanup={'status':'pass'})))
            raise ContractError('synthetic observer could not interpret an emitted event')
        with tempfile.TemporaryDirectory() as folder:
            result=document_native(executor,Path(folder),Path(folder)/'native',self.state(),
                                   deadline=1,binding_plan=True)
        self.assertEqual(result['status'],'checker_error')
        self.assertTrue(result['native_candidate_executed'])
        self.assertEqual(result['execution']['cleanup']['status'],'pass')


if __name__=='__main__':
    unittest.main()
