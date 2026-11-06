"""Register temporaries and explicit persistent FSM state stay distinct."""
import unittest

from method.autodriver.implementation.system.contract.model import parse,ContractError
from method.autodriver.implementation.system.contract.fsm import Interpreter,InputEvent


def contract(into='raw_calibration'):
    return parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},FSM('idle',"
      "[State('idle'),State('ready'),State('done')],["
      "Transition('idle',EventRef('initialize','logical_success'),'ready',action=ReadRegister(3,8,'big',"+repr(into)+"),"
      "effects=[Assign('calibration','raw_calibration'),Assign('ready', 'True')]),"
      "Transition('ready',EventRef('acquire','logical_success'),'done',guard='ready',"
      "action=Sequence(ReadFrame('sample',name='read'),CommitValidatedSample('sample')))],"
      "variables={'calibration':{'type':'int','initial':0},'ready':{'type':'bool','initial':False}},accepting=['done']),"
      "frames={'sample':FrameLayout(1,{'raw':Field(0,8,'big')})})",'dbc')


class RegisterIdentityTests(unittest.TestCase):
    def test_only_declared_state_persists_into_a_new_explicit_fsm_acquisition(self):
        machine=Interpreter(contract())
        machine.feed(InputEvent('initialize','logical_success','init',0))
        operation=next(e.event[0] for e in machine.graph.edges if e.source==machine.state and e.event and e.event[1]=='physical_start')
        machine.feed(InputEvent(operation,'physical_start','init',0))
        machine.feed(InputEvent(operation,'physical_success','init',0,[7]))
        self.assertEqual(machine.variables['raw_calibration'],7);self.assertEqual(machine.variables['calibration'],7)
        machine.feed(InputEvent('acquire','logical_success','one',0))
        self.assertNotIn('raw_calibration',machine.variables);self.assertEqual(machine.variables['calibration'],7)
        self.assertEqual(machine.instance,'one')
        machine.feed(InputEvent('read','physical_start','one',0));machine.feed(InputEvent('read','physical_success','one',0,[9]))
        self.assertEqual(machine.sample['sample'].instance,'one');self.assertEqual(machine.sample['sample'].fields['raw'],9)
        with self.assertRaises(ContractError):machine.begin_acquisition('one')

    def test_register_integer_does_not_silently_coerce_declared_boolean_state(self):
        machine=Interpreter(contract(into='ready'))
        machine.feed(InputEvent('initialize','logical_success','init',0))
        operation=next(e.event[0] for e in machine.graph.edges if e.source==machine.state and e.event and e.event[1]=='physical_start')
        machine.feed(InputEvent(operation,'physical_start','init',0))
        with self.assertRaisesRegex(ContractError,'boolean FSM'):machine.feed(InputEvent(operation,'physical_success','init',0,[1]))
