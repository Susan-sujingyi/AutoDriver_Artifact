import json
from pathlib import Path
import tempfile
import unittest

from method.autodriver.implementation.system.contract.model import parse,ContractError
from method.autodriver.implementation.system.document_projection import executable_view
from method.autodriver.implementation.system.native.framework_probe import evaluate,profile
from method.autodriver.implementation.system.protocol.assembler import digest
from tests.common.autodriver_document_native import document_native


FP="FrameworkProfile('tock',Ref('scope'),i2c=AsyncI2CContract({}, {}, {}, [DeviceEnabled()], Sequence(HoldsBufferUntilCompletion()), ReturnsErrorAndBuffer(), Callback('done',{},resource_effect=ReturnsBufferToClient()), Unspecified()))"


class FrameworkNativeProbeTests(unittest.TestCase):
    def trace(self, phases):
        return '\n'.join(map(json.dumps,[dict(phase='case_begin',case='one'),
            *[dict(tick=i,frequency=1000000,**v) for i,v in enumerate(phases)],dict(phase='case_end')]))

    def test_fp_enable_requirement_produces_a_finite_violation_without_device_model(self):
        events=[dict(phase='submit',operation=1,buffer=1),dict(phase='accepted',operation=1,buffer=1),
                dict(phase='callback_failure',operation=1,buffer=1,returned_buffer=1)]
        result=evaluate(None,parse(FP,'fp'),dict(cases=[dict(case='one')]),self.trace(events))
        self.assertEqual(result['status'],'violated')
        self.assertIn('DeviceEnabled',result['cases'][0]['violations'][0])
        self.assertEqual(result['device_contract_status'],'not_checked')
        enabled=evaluate(None,parse(FP,'fp'),dict(cases=[dict(case='one')]),
            self.trace([dict(phase='enable'),*events]))
        self.assertEqual(enabled['status'],'unknown')
        self.assertEqual(enabled['cases'][0]['violations'],[])

    def test_absent_fp_does_not_invent_enable_guarantee(self):
        result=evaluate(None,None,dict(cases=[dict(case='one')]),self.trace([dict(phase='submit',operation=1,buffer=1)]))
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(result['cases'][0]['violations'],[])

    def test_spi_does_not_consume_unrelated_i2c_preconditions_from_a_broad_fp(self):
        result=evaluate(None,parse(FP,'fp'),dict(bus='spi',cases=[dict(case='one')]),
            self.trace([dict(phase='submit',operation=1,write_buffer=1,read_buffer=2)]))
        self.assertEqual(result['status'],'unknown')
        self.assertEqual(result['cases'][0]['violations'],[])

    def test_native_registration_rejection_is_a_diagnostic_without_measurement_stimuli(self):
        result=evaluate(None,None,dict(cases=[dict(case='one')]),
            self.trace([dict(phase='registration_rejected',reason='duplicate_type',type=0)]))
        self.assertEqual(result['status'],'violated')
        self.assertIn('duplicate_type',result['cases'][0]['violations'][0])

    def test_parse_available_does_not_claim_unsupported_variables_are_executable(self):
        text="DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence(),variables={'opaque':{'type':'sample_or_empty','initial':'empty'}}))"
        content,report=executable_view(text,'dbc')
        self.assertIsNotNone(content)
        self.assertEqual(report['status'],'available')
        self.assertEqual(report['execution_capability'],'unsupported')

    def test_missing_device_view_can_execute_separate_public_probe_and_keeps_the_gap(self):
        state=dict(objects={'dbc':dict(material_id='dbc'),'target_fp':dict(material_id='fp')},
            materials=[dict(id='dbc',text='Readable device model',sha256='1'*64),
                       dict(id='fp',text=FP,sha256='2'*64)])
        seen=[]
        def full(*args,**kwargs):self.fail('no full-device view exists')
        def public(candidate,destination,state,**kwargs):
            destination.mkdir()
            seen.append(state)
            return dict(status='observed',native_candidate_executed=True,
                checks=dict(status='violated',device_contract_status='not_checked'),
                execution=dict(cleanup=dict(status='pass')))
        with tempfile.TemporaryDirectory() as tmp:
            result=document_native(full,Path(tmp),Path(tmp)/'native',state,deadline=1,
                binding_plan=False,framework_executor=public)
        self.assertEqual(len(seen),1)
        self.assertTrue(result['native_candidate_executed'])
        self.assertEqual(result['device_contract_execution']['status'],'not_checked')
        self.assertEqual(result['observation_path'],'framework-probe/run-process/stdout.jsonl')
        self.assertEqual(result['execution']['cleanup']['status'],'pass')

    def test_public_probe_observer_error_preserves_real_execution_and_cleanup(self):
        state=dict(objects={'dbc':dict(material_id='dbc'),'target_fp':dict(material_id='fp')},
            materials=[dict(id='dbc',text='Readable device model',sha256='1'*64),
                       dict(id='fp',text=FP,sha256='2'*64)])
        def full(*args,**kwargs):self.fail('no full device view')
        def public(candidate,destination,state,**kwargs):
            (destination/'run-process').mkdir(parents=True)
            (destination/'run-process/process.json').write_text(json.dumps(dict(status='pass',cleanup=dict(status='pass'))))
            raise ContractError('synthetic public observer gap after actual execution')
        with tempfile.TemporaryDirectory() as tmp:
            result=document_native(full,Path(tmp),Path(tmp)/'native',state,deadline=1,
                binding_plan=False,framework_executor=public)
        self.assertEqual(result['status'],'checker_error')
        self.assertTrue(result['native_candidate_executed'])
        self.assertEqual(result['execution']['cleanup']['status'],'pass')

    def test_lowering_gap_fallback_resolves_compiled_fp_against_original_primary(self):
        dbc="DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence(),variables={'opaque':{'type':'sample_or_empty','initial':'empty'}}))"
        primary='Readable framework primary with conditional semantics.'
        material=dict(id='fp',text=primary,sha256=digest(primary.encode()))
        view=dict(schema='autodriver.derived-executable-view/v1',object='target_fp',
            primary_material_id='fp',primary_material_sha256=material['sha256'],normative=False,content=FP)
        state=dict(objects={'dbc':dict(material_id='dbc'),'target_fp':dict(material_id='fp')},
            derived_views={'target_fp':'fp-view'},materials=[dict(id='dbc',text=dbc,sha256=digest(dbc.encode())),
                material,dict(id='fp-view',text=json.dumps(view),parents=['fp'])])
        def full(candidate,destination,projected,**kwargs):
            self.assertNotEqual(projected['objects']['target_fp']['material_id'],'fp')
            destination.mkdir()
            raise ContractError('unsupported variable declaration')
        def public(candidate,destination,actual,**kwargs):
            self.assertEqual(actual['objects']['target_fp']['material_id'],'fp')
            self.assertEqual(profile(actual),parse(FP,'fp'))
            destination.mkdir()
            return dict(status='observed',native_candidate_executed=True,checks=dict(status='unknown'))
        with tempfile.TemporaryDirectory() as tmp:
            result=document_native(full,Path(tmp),Path(tmp)/'native',state,deadline=1,
                binding_plan=False,framework_executor=public)
        self.assertTrue(result['native_candidate_executed'])
        self.assertEqual(result['checks']['status'],'unknown')
        self.assertEqual(result['device_contract_execution']['reason'],'unsupported variable declaration')


if __name__=='__main__':unittest.main()
