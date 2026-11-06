"""Finite state/register witnesses, generated from synthetic contracts only."""
import unittest

from method.autodriver.implementation.system.contract.model import parse,Unknown
from method.autodriver.implementation.system.contract.solver import solve_witness
from method.autodriver.implementation.system.native.prefix import success_prefix,wire_response


ENC="{'address_bits':8,'address_byte_order':'big','read_header':['address'],'write_header':['address'],'dummy_read_bytes':0,'auto_increment':False}"
FRAME="frames={'sample':FrameLayout(1,{'raw':Field(0,8,'big')})}"


class SSAWitnessTests(unittest.TestCase):
    def test_parallel_state_assignments_persist_but_register_temporary_does_not(self):
        root=parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},FSM('idle',[State('idle'),State('ready'),State('done')],["
            "Transition('idle',EventRef('initialize','logical_success'),'ready',action=ReadRegister(5,8,'big','raw_cal',encoding="+ENC+"),"
            "effects=[Assign('a','raw_cal'),Assign('b','a'),Assign('ready','True')]),"
            "Transition('ready',EventRef('acquire','logical_success'),'done',guard='ready and a == 17 and b == 2',"
            "action=Sequence(ReadFrame('sample',name='read'),Choice(Case('raw > 0 and 1/raw > 0',CommitValidatedSample('sample')),otherwise=FailCurrentOperation(True))))],"
            "variables={'a':{'type':'int','initial':2},'b':{'type':'int','initial':0},'ready':{'type':'bool','initial':False}},accepting=['done']),"+FRAME+")",'dbc')
        prefix=success_prefix(root,'i2c')
        self.assertEqual(prefix['steps'][0]['wire'].write,(5,));self.assertEqual(wire_response(prefix['steps'][0],prefix['frames']),[17])
        self.assertEqual([s['phase'] for s in prefix['steps']],['initialization','acquisition'])
        self.assertGreater(prefix['frames']['sample'][0],0)
        self.assertIn('interpreter-checked',prefix['scope'])

    def test_signed_register_witness_and_old_temporary_guard(self):
        def model(guard):
            return parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle("
                "Sequence(ReadRegister(3,8,'big','id',encoding="+ENC+"),Choice(Case('id == 17',Sequence()),otherwise=FailCurrentOperation(True))),"
                "Sequence(ReadRegister(4,8,'big','status',signed=True,encoding="+ENC+"),ReadFrame('sample'),Choice(Case("+repr(guard)+",CommitValidatedSample('sample')),otherwise=FailCurrentOperation(True))),recovery=None,reentry=None),"+FRAME+")",'dbc')
        prefix=success_prefix(model('status == -1'),'i2c')
        self.assertEqual(wire_response(prefix['steps'][0],prefix['frames']),[17])
        self.assertEqual(wire_response(prefix['steps'][1],prefix['frames']),[255])
        with self.assertRaises(Unknown):success_prefix(model('id == 17'),'i2c')

    def test_bit_guard_uses_declared_register_width_without_machine_promotion(self):
        def model(guard):
            return parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence("
                "ReadRegister(4,8,'big','status',encoding="+ENC+"),ReadFrame('sample'),Choice(Case("+repr(guard)+
                ",CommitValidatedSample('sample')),otherwise=FailCurrentOperation(True))),recovery=None,reentry=None),"+FRAME+")",'dbc')
        prefix=success_prefix(model('(status & 9) == 0 and status > 0'),'i2c')
        value=wire_response(prefix['steps'][0],prefix['frames'])[0]
        self.assertGreater(value,0);self.assertEqual(value&9,0)
        with self.assertRaises(Unknown):success_prefix(model('((status + 1) & 9) == 0'),'i2c')

    def test_deadline_is_replayed_with_minimum_waits_without_a_hardware_claim(self):
        def model(wait):
            return parse("DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence("
                "WriteBytes([2],name='command'),WaitAfterCompletion('command',"+repr(wait)+"),"
                "Repeat(ReadRegister(4,8,'big','status',encoding="+ENC+"),'status == 0',maximum_attempts=2,deadline=Deadline(EventRef('acquire','logical_success'),'1 ms')),"
                "ReadFrame('sample'),CommitValidatedSample('sample')),recovery=None,reentry=None),"+FRAME+")",'dbc')
        self.assertIn('zero I/O latency',success_prefix(model('0.5 ms'),'i2c')['scope'])
        with self.assertRaises(Unknown):success_prefix(model('1 ms'),'i2c')

    def test_existential_query_does_not_mistake_undefined_counterexample_for_input(self):
        result=solve_witness('flag and value > 0',{'raw':'int'},['raw>=0','raw<=3'],
            definitions={'value':'1/raw','flag':'raw>0'},definition_types={'value':'real','flag':'bool'})
        self.assertEqual(result['status'],'satisfied');self.assertNotEqual(result['assignment']['raw'],'0')
        impossible=solve_witness('value > 0',{'raw':'int'},['raw==0'],definitions={'value':'1/raw'})
        self.assertEqual(impossible['status'],'violated');self.assertIsNone(impossible['assignment'])
