"""Current completeness is observed metadata, never implicit calibration truth."""
import unittest

from method.autodriver.implementation.system.contract.model import parse,ContractError,Unknown,node
from method.autodriver.implementation.system.contract.data import decode_frame,frame_environment,crc8,DataInvalid
from method.autodriver.implementation.system.contract.domains import domains_from_frames
from method.autodriver.implementation.system.contract.relations import finite_value,Relations
from method.autodriver.implementation.system.contract.fsm import Interpreter,InputEvent
from tests.regression.test_autodriver_lifecycle_state import CONTENT


class FrameStatusTests(unittest.TestCase):
    def test_missing_old_misnamed_partial_and_bad_integrity_never_become_complete(self):
        layout=parse("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),frames={'sample':FrameLayout(2,{'raw':Field(0,8,'big'),'crc':Field(8,8,'big')},integrity=[Checksum(CRC8(49,255),[(0,1)],'crc')])})",'dbc')['frames']['sample']
        checksum=crc8(bytes([7]),layout['integrity'][0]['algorithm'])
        frame=decode_frame(layout,bytes([7,checksum]),name='sample',instance='current')
        key='frame_status.sample.complete_current'
        for frames,instance in [({},'current'),({'sample':frame},'later'),({'other':frame},'current')]:
            env=frame_environment(frames,instance,declared_frames=['sample'])
            self.assertFalse(env[key]);self.assertNotIn('sample.raw',env)
        for data in (bytes([7]),bytes([7,checksum^1])):
            with self.assertRaises(DataInvalid):decode_frame(layout,data,name='sample',instance='current')
        env=frame_environment({'sample':frame},'current',declared_frames=['sample'])
        self.assertTrue(env[key]);self.assertEqual(env['sample.raw'],7)
        with self.assertRaises(ContractError):frame_environment({'sample':frame},'current',{'frame_status':True})

    def test_complete_frame_does_not_establish_calibration_or_source_validity(self):
        root=parse(CONTENT,'dbc');frame=decode_frame(root['frames']['sample'],bytes([3]),name='sample',instance='one')
        quantity=node('PhysicalQuantity',expression='sample.raw',requires=('frame_status.sample.complete_current','calibration_valid'))
        env=frame_environment({'sample':frame},'one',declared_frames=root['frames'])
        with self.assertRaises(Unknown):finite_value(quantity,env)
        with self.assertRaises(DataInvalid):finite_value(quantity,{**env,'calibration_valid':False})
        self.assertEqual(finite_value(quantity,{**env,'calibration_valid':True}),3)
        stale=frame_environment({'sample':frame},'two',{'calibration_valid':True},declared_frames=root['frames'])
        with self.assertRaises(DataInvalid):finite_value(quantity,stale)

    def test_same_global_interpreter_clears_frame_status_but_keeps_only_explicit_state(self):
        text=CONTENT.replace("requires=['initialized']","requires=['initialized','frame_status.sample.complete_current']")
        root=parse(text,'dbc');machine=Interpreter(root)
        for operation,phase,tick,data in [('initialize','logical_success',0,None),('read_cal','physical_start',1,None),('read_cal','physical_success',2,bytes([7]))]:
            machine.feed(InputEvent(operation,phase,'init',tick,data))
        self.assertTrue(machine.guard('frame_status.cal.complete_current'))
        machine.feed(InputEvent('acquire','logical_success','measurement',3))
        self.assertFalse(machine.guard('frame_status.cal.complete_current'))
        self.assertFalse(machine.guard('frame_status.sample.complete_current'))
        for operation,phase,tick,data in [('read_sample','physical_start',4,None),('read_sample','physical_success',5,bytes([3]))]:
            machine.feed(InputEvent(operation,phase,'measurement',tick,data))
        env=frame_environment(machine.sample,machine.instance,machine.sample_variables,declared_frames=root['frames'])
        self.assertTrue(env['frame_status.sample.complete_current']);self.assertFalse(env['frame_status.cal.complete_current'])
        self.assertTrue(env['initialized']);self.assertEqual(Relations(root['quantities'],external_names=set(env)).value('value',env),21)
        domains=domains_from_frames(root)
        self.assertEqual(domains['frame_status.sample.complete_current'],{'type':'bool','bounds':(False,True)})
