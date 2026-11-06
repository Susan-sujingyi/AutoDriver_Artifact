"""Contract-derived inputs and current-data guards; no private fixtures."""
import unittest

from method.autodriver.implementation.system.contract.model import parse,ContractError,Unknown
from method.autodriver.implementation.system.contract.data import decode_frame,frame_environment,DataInvalid
from method.autodriver.implementation.system.contract.stimuli import build_frame,frame_cases
from method.autodriver.implementation.system.contract.fsm import Interpreter,InputEvent


def model(behavior):
    return parse("DeviceContract('synthetic',I2C(7),FrozenPolicy(),{},"+behavior+",frames={'f':FrameLayout(4,{'a':Field(0,16,'little',signed=True),'b':Field(16,8,'big'),'sum':Field(24,8,'big')},integrity=[Checksum(CRC8(7,0),[(0,3)],'sum')])})",'dbc')


class StimulusTests(unittest.TestCase):
    def test_native_byte_order_and_integrity_generated_from_contract(self):
        dbc=model('Lifecycle(Sequence(),Sequence())');layout=dbc['frames']['f']
        data=build_frame(layout,{'a':-3,'b':129},name='f',instance='one')
        self.assertEqual(data[:3],bytes((0xfd,0xff,0x81)))
        frame=decode_frame(layout,data,name='f',instance='one');self.assertEqual(frame.fields['a'],-3)
        with self.assertRaises(ContractError):build_frame(layout,{'a':-3},name='f')

    def test_boundary_and_checksum_fault_cases_retain_scope(self):
        dbc=model('Lifecycle(Sequence(),Sequence())');report=frame_cases(dbc)
        self.assertEqual(len(report['cases']),4)
        for case in report['cases']:
            if case['expected_valid']:decode_frame(dbc['frames']['f'],case['data'],name='f',instance='one')
            else:
                with self.assertRaises(DataInvalid):decode_frame(dbc['frames']['f'],case['data'],name='f',instance='one')
        with self.assertRaises(Unknown):frame_cases(dbc,maximum_cases=2)
        self.assertIn('not native execution',report['scope'])

    def test_old_frames_and_ambiguous_short_names_cannot_supply_new_values(self):
        dbc=model('Lifecycle(Sequence(),Sequence())');layout=dbc['frames']['f'];data=build_frame(layout,{'a':2,'b':3})
        one=decode_frame(layout,data,name='f',instance='one');other=decode_frame(layout,data,name='g',instance='one')
        stale=frame_environment({'f':one},'two')
        self.assertEqual(stale,{'frame_status.f.complete_current':False})
        env=frame_environment({'f':one,'g':other},'one')
        self.assertNotIn('a',env);self.assertEqual(env['f.a'],2)

    def test_global_fsm_guard_uses_only_current_validated_field(self):
        behavior="Lifecycle(Sequence(),Sequence(ReadFrame('f',name='read',boundary='stop'),Choice(Case('f.b >= 5',WriteBytes([1],name='selected',boundary='stop')),otherwise=FailCurrentOperation(True))),on_failure=FailCurrentOperation(True))"
        for value,expected in [(7,True),(3,False)]:
            dbc=model(behavior);machine=Interpreter(dbc);data=build_frame(dbc['frames']['f'],{'a':0,'b':value})
            for op,phase,time,payload in [('initialize','logical_success',0,None),('acquire','logical_success',1,None),('read','physical_start',2,None),('read','physical_success',3,data)]:
                machine.feed(InputEvent(op,phase,'one',time,payload))
            if expected:
                machine.feed(InputEvent('selected','physical_start','one',4));machine.feed(InputEvent('selected','physical_success','one',5))
                self.assertTrue(machine.result()['operation_succeeded'])
            else:self.assertEqual(machine.result()['state'],'failure')

    def test_parallel_effects_can_read_successful_action_data(self):
        dbc=model("FSM('a',[State('a'),State('b')],[Transition('a','request','b',action=ReadFrame('f',name='read',boundary='stop'),effects=[Assign('x','f.b')])],variables={'x':{'type':'int','initial':0}},accepting=['b'])")
        machine=Interpreter(dbc);data=build_frame(dbc['frames']['f'],{'a':0,'b':11})
        machine.feed(InputEvent('request','logical_success','one',0));machine.feed(InputEvent('read','physical_start','one',1));machine.feed(InputEvent('read','physical_success','one',2,data))
        self.assertEqual(machine.variables['x'],11)


if __name__=='__main__':unittest.main()
