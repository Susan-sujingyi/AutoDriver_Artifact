"""Lifecycle state lowers into the same FSM, with no implicit calibration."""
import unittest

from method.autodriver.implementation.system.contract.model import parse,ContractError
from method.autodriver.implementation.system.contract.fsm import Interpreter,InputEvent,lower
from method.autodriver.implementation.system.contract.check import check_contract
from method.autodriver.implementation.system.contract.relations import Relations
from method.autodriver.implementation.system.contract.data import frame_environment
from method.autodriver.implementation.system.native.prefix import success_prefix


CONTENT="""DeviceContract('synthetic',I2C(7),FrozenPolicy(),{},
Lifecycle(Sequence(ReadFrame('cal',name='read_cal'),Assign('gain','cal.gain'),Assign('initialized','True')),
Sequence(ReadFrame('sample',name='read_sample'),CommitValidatedSample('sample')),
on_failure=FailCurrentOperation(True),recovery=None,reentry=None,
variables={'gain':{'type':'int','initial':0},'initialized':{'type':'bool','initial':False}}),
frames={'cal':FrameLayout(1,{'gain':Field(0,8,'big')}),'sample':FrameLayout(1,{'raw':Field(0,8,'big')})},
quantities={'value':PhysicalQuantity('gain*sample.raw','Pa',requires=['initialized'])})"""


class LifecycleStateTests(unittest.TestCase):
    def initialization(self,machine,*,fail=False):
        for operation,phase,tick,data in [('initialize','logical_success',0,None),('read_cal','physical_start',1,None),
                ('read_cal','physical_failure' if fail else 'physical_success',2,None if fail else bytes([7]))]:
            machine.feed(InputEvent(operation,phase,'init',tick,data))

    def test_only_explicit_assigned_state_survives_new_acquisition(self):
        root=parse(CONTENT,'dbc');machine=Interpreter(root);self.assertFalse(machine.variables['initialized'])
        self.initialization(machine);self.assertEqual(machine.variables['gain'],7)
        for operation,phase,tick,data in [('acquire','logical_success',3,None),('read_sample','physical_start',4,None),('read_sample','physical_success',5,bytes([3]))]:
            machine.feed(InputEvent(operation,phase,'measurement',tick,data))
        environment=frame_environment(machine.sample,machine.instance,machine.sample_variables)
        self.assertNotIn('cal.gain',environment);self.assertEqual(environment['gain'],7)
        self.assertEqual(Relations(root['quantities'],external_names=set(environment)).value('value',environment),21)
        prefix=success_prefix(root,'i2c');self.assertIn('gain',prefix['bound_input_names']);self.assertNotIn('cal.gain',prefix['bound_input_names'])
        report=check_contract(CONTENT,'dbc')[1]
        self.assertTrue(any(f['check']=='finite_quantity_input_binding' and f['status']=='satisfied' for f in report['findings']))

    def test_failed_initialization_never_runs_following_state_assignments(self):
        machine=Interpreter(parse(CONTENT,'dbc'));self.initialization(machine,fail=True)
        self.assertFalse(machine.variables['initialized']);self.assertEqual(machine.variables['gain'],0)
        self.assertEqual(machine.result()['state'],'failure')
        with self.assertRaises(ContractError):machine.feed(InputEvent('acquire','logical_success','new',3))

    def test_sequential_assignment_is_distinct_from_parallel_effects_and_types_remain_checked(self):
        root=parse("DeviceContract('synthetic',I2C(7),FrozenPolicy(),{},Lifecycle(Sequence(Assign('x','x+1'),Assign('y','x')),Sequence(),variables={'x':{'type':'int','initial':0},'y':{'type':'int','initial':0}}))",'dbc')
        machine=Interpreter(root);machine.feed(InputEvent('initialize','logical_success','one',0))
        self.assertEqual(machine.variables['x'],1);self.assertEqual(machine.variables['y'],1)
        wrong=CONTENT.replace("Assign('gain','cal.gain')","Assign('gain','True')")
        with self.assertRaises(ContractError):self.initialization(Interpreter(parse(wrong,'dbc')))
        undeclared=CONTENT.replace("Assign('gain','cal.gain')","Assign('missing','cal.gain')")
        with self.assertRaises(ContractError):lower(parse(undeclared,'dbc')['behavior'])
        old=parse("DeviceContract('synthetic',I2C(7),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))",'dbc')
        self.assertEqual(dict(old['behavior']['variables']),{});self.assertEqual(lower(old['behavior']).variables,{})
