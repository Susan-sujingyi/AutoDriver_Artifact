"""Synthetic semantic counterexamples, never benchmark-private assertions."""
import unittest
from fractions import Fraction

from method.autodriver.implementation.system.contract.model import parse,node,ContractError,Unknown,References,Pinned,identity
from method.autodriver.implementation.system.contract.expression import evaluate,scalar,quantity,seconds
from method.autodriver.implementation.system.contract.data import decode_frame,crc8,commit,encode
from method.autodriver.implementation.system.contract.timing import Event,wait_elapsed,required_ticks,check_tick_choice,callback_anchor
from method.autodriver.implementation.system.contract.fsm import lower,Interpreter,InputEvent
from method.autodriver.implementation.system.contract.native import NativeMonitor
from method.autodriver.implementation.system.contract.check import check_contract


def contract(behavior, **extra):
    return parse("DeviceContract('synthetic-device',I2C(17),FrozenPolicy(mode='given'),{},"+behavior+
                 (","+",".join(k+"="+v for k,v in extra.items()) if extra else "")+")", "dbc")


def api():
    return node("APIContract",accepted=node("HoldsBufferUntilCompletion"),rejected=node("ReturnsErrorAndBuffer"),
                completion=node("Callback",resource_effect=node("ReturnsBufferToClient")))


class ParserTests(unittest.TestCase):
    def test_immutable_defaults_and_exact_constants(self):
        model=contract("Lifecycle(Sequence(),Sequence())")
        self.assertEqual(model["behavior"]["recovery"].name,"Unspecified")
        with self.assertRaises(TypeError): model.fields["behavior"]=None
        self.assertEqual(parse("FrameworkProfile('os',Ref('snapshot'),outputs={'a':0.125})","fp")["outputs"]["a"],Fraction(1,8))

    def test_untrusted_syntax_rejected(self):
        examples=["__import__('os')", "DeviceContract.__class__", "DeviceContract(**{})",
                  "DeviceContract('x',I2C(True),FrozenPolicy(),{},Sequence(),frames={'x':1,'x':2})",
                  "DeviceContract('x',I2C(1),FrozenPolicy(),{},[x for x in []])"]
        for text in examples:
            with self.subTest(text=text),self.assertRaises(ContractError): parse(text,"dbc")

    def test_pin_and_exact_path(self):
        model=contract("Lifecycle(Sequence(),Sequence())")
        refs=References({'dbc':Pinned(model,identity(model))},{'dbc':identity(model)})
        self.assertEqual(refs.resolve('dbc.connection.address_7bit'),17)
        with self.assertRaises(Unknown): refs.resolve('dbc.connections')
        with self.assertRaises(Unknown): References({'dbc':Pinned(model,identity(model))},{'dbc':'0'*64}).resolve('dbc')

    def test_boolean_is_not_i2c_integer(self):
        _,report=check_contract("DeviceContract('x',I2C(True),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()))","dbc")
        self.assertEqual(report['status'],'violated')

    def test_unresolved_never_passes(self):
        _,report=check_contract("FrameworkProfile('x',Ref('snapshot'))","fp")
        self.assertEqual(report['status'],'unknown')


class ExpressionTests(unittest.TestCase):
    def test_exact_division_and_negative_floor(self):
        self.assertEqual(scalar('1/3+2/3'),1)
        self.assertEqual(scalar('-3//2'),-2)
        self.assertEqual(scalar('0.1+0.2'),Fraction(3,10))

    def test_dimensions_and_ticks(self):
        env={'a':quantity('1 ms'),'b':quantity('1000 us'),'n':quantity('1 tick')}
        self.assertTrue(evaluate('a == b',env))
        with self.assertRaises(ContractError): evaluate('a >= n',env)
        with self.assertRaises(ContractError): seconds(1)
        with self.assertRaises(ContractError): evaluate('a+1',env)

    def test_unsafe_or_unknown_expression(self):
        for text in ("open('/etc/passwd')",'x.__class__', '(lambda: 1)()', '[x for x in y]'):
            with self.subTest(text=text),self.assertRaises(ContractError): evaluate(text)
        with self.assertRaises(Unknown): evaluate('missing + 1')
        with self.assertRaises(ContractError): evaluate('1/0')
        with self.assertRaises(Unknown): evaluate('sqrt(2)')
        self.assertEqual(scalar('sqrt(4)'),2)

    def test_bitvectors_and_shift_bounds(self):
        self.assertEqual(scalar('signed(extract(x,8,8),8)',{'x':0xff00},bit_width=16),-1)
        with self.assertRaises(ContractError): scalar('1 << 8',bit_width=8)
        with self.assertRaises(Unknown): scalar('1 & 2')
        with self.assertRaises(ContractError): scalar('255 << 1',bit_width=8)

    def test_three_valued_boolean(self):
        self.assertFalse(evaluate('missing and False'))
        self.assertTrue(evaluate('missing or True'))
        with self.assertRaises(Unknown): evaluate('missing and True')
        with self.assertRaises(ContractError): evaluate('1 and True')


class TimingTests(unittest.TestCase):
    def test_submission_is_not_completion(self):
        start=Event('send','submit','a',0)
        done=Event('send','physical_success','a',Fraction(1,1000))
        read=Event('read','physical_start','a',Fraction(5,1000))
        with self.assertRaises(ContractError): wait_elapsed(start,read,'5 ms')
        with self.assertRaises(ContractError): wait_elapsed(done,read,'5 ms')
        self.assertEqual(wait_elapsed(done,Event('read','physical_start','a',Fraction(6,1000)),'5 ms'),Fraction(5,1000))

    def test_floor_conversion_and_phase_are_not_safe(self):
        n=required_ticks('1 ms',32768,1,minimum_ticks=2,maximum_ticks=1000)
        self.assertEqual(n,34)
        with self.assertRaises(ContractError): check_tick_choice('1 ms',32,32768,1,minimum_ticks=2,maximum_ticks=1000,
            reference_not_before_anchor=True,callback_not_before_deadline=True)
        self.assertEqual(check_tick_choice('1 ms',34,32768,1,minimum_ticks=2,maximum_ticks=1000,
            reference_not_before_anchor=True,callback_not_before_deadline=True)['status'],'satisfied')

    def test_unknown_provider_and_counter_saturation(self):
        with self.assertRaises(Unknown): required_ticks('1 ms',None,1,minimum_ticks=2,maximum_ticks=100)
        with self.assertRaises(ContractError): required_ticks('1 s',1000,1,minimum_ticks=2,maximum_ticks=100)
        with self.assertRaises(Unknown): check_tick_choice('1 ms',34,32768,1,minimum_ticks=2,maximum_ticks=100,
            reference_not_before_anchor=False,callback_not_before_deadline=True)

    def test_stale_callback_and_unknown_relation(self):
        physical=Event('send','physical_success','a',1)
        with self.assertRaises(ContractError): callback_anchor(physical,Event('send','callback_success','b',2),provider_relation='device_not_after_native')
        with self.assertRaises(Unknown): callback_anchor(physical,Event('send','callback_success','a',2),provider_relation=None)


class DataTests(unittest.TestCase):
    def setUp(self):
        self.algorithm=node('CRC8',polynomial=7,initial=0,reflect_input=False,reflect_output=False,final_xor=0)
        self.layout=node('CRCProtectedWords',names=('a','b'),encoding={'width':8,'signed':False,'byte_order':'big'},crc=self.algorithm)
        self.data=bytes((12,crc8(bytes([12]),self.algorithm),34,crc8(bytes([34]),self.algorithm)))

    def test_complete_all_integrity_before_commit(self):
        f=decode_frame(self.layout,self.data,name='pair',instance='one')
        self.assertEqual(f.fields['b'],34)
        self.assertEqual(commit({'pair':f},('pair',),'one')['pair'],f)
        corrupt=bytearray(self.data);corrupt[-1]^=1
        with self.assertRaises(ContractError): decode_frame(self.layout,bytes(corrupt),name='pair',instance='one')
        with self.assertRaises(ContractError): decode_frame(self.layout,self.data[:-1],name='pair',instance='one')
        with self.assertRaises(ContractError): commit({'pair':f},('pair',),'two')

    def test_overlap_and_bad_checksum_ranges(self):
        field=node('Field',offset=0,width=8,byte_order='big',signed=False)
        bad=node('FrameLayout',length=1,fields={'a':field,'b':field},integrity=(),completeness='exact')
        with self.assertRaises(ContractError): decode_frame(bad,b'\x00',name='x',instance='one')

    def test_native_scale_units_rounding_and_range(self):
        encoding=node('OutputEncoding',storage_type='i16',unit='Pa',scale=Fraction(1,10),offset=0,range=(-32768,32767))
        rounding=node('Rounding',mode='nearest_ties_even',overflow='report_gap')
        self.assertEqual(encode(Fraction(201,100),'Pa',encoding,rounding,Fraction(1,20))['stored'],20)
        with self.assertRaises(ContractError): encode(2,'°C',encoding,rounding,Fraction(1,20))
        with self.assertRaises(ContractError): encode(100000,'Pa',encoding,rounding,Fraction(1,20))
        with self.assertRaises(ContractError): encode(Fraction(201,100),'Pa',encoding,rounding,0)


class NativeTests(unittest.TestCase):
    def test_typed_resource_effects_can_be_declared_separately_from_native_return_values(self):
        operation=node('APIContract',accepted='Ok',rejected=node('ReturnsErrorAndBuffer'),
            completion=node('Callback',resource_effect=node('ReturnsBufferToClient')),
            resource_effect=node('HoldsBufferUntilCompletion'))
        compact=node('AsyncI2CContract',accepted='Ok',rejected='Err',completion='callback',write=operation,read=operation,write_read=None)
        monitor=NativeMonitor(compact)
        monitor.feed(operation='io',instance='one',phase='submit',buffer='buf')
        monitor.feed(operation='io',instance='one',phase='accepted')
        with self.assertRaises(ContractError):monitor.feed(operation='io',instance='one',phase='use_buffer',buffer='buf')
        monitor.feed(operation='io',instance='one',phase='callback_success',returned_buffer='buf')
        self.assertEqual(monitor.result()['status'],'satisfied')

    def test_unstructured_resource_effect_is_unknown_and_not_a_false_leak(self):
        unstructured=node('APIContract',accepted='takes buffer',rejected='returns it',completion='callback returns it')
        monitor=NativeMonitor(unstructured)
        for instance in ('one','two'):
            monitor.feed(operation='io',instance=instance,phase='submit',buffer='buf')
            monitor.feed(operation='io',instance=instance,phase='accepted')
            monitor.feed(operation='io',instance=instance,phase='callback_success',returned_buffer='buf')
        result=monitor.result();self.assertEqual(result['status'],'unknown')
        self.assertEqual(result['held_buffers'],[]);self.assertTrue(result['gaps'])

    def test_immediate_failure_must_return_original_buffer(self):
        monitor=NativeMonitor(api())
        monitor.feed(operation='io',instance='one',phase='submit',buffer='buffer')
        with self.assertRaises(ContractError): monitor.feed(operation='io',instance='one',phase='rejected')
        monitor.feed(operation='io',instance='one',phase='rejected',returned_buffer='buffer')
        self.assertEqual(monitor.result()['status'],'satisfied')

    def test_accepted_failure_is_not_sync_rejection(self):
        monitor=NativeMonitor(api())
        monitor.feed(operation='io',instance='one',phase='submit',buffer='buffer')
        monitor.feed(operation='io',instance='one',phase='accepted')
        with self.assertRaises(ContractError): monitor.feed(operation='io',instance='one',phase='rejected',returned_buffer='buffer')
        with self.assertRaises(ContractError): monitor.feed(operation='io',instance='one',phase='use_buffer',buffer='buffer')
        monitor.feed(operation='io',instance='one',phase='callback_failure',returned_buffer='buffer')
        with self.assertRaises(ContractError): monitor.feed(operation='io',instance='one',phase='commit',required_frames=('frame',))

    def test_stale_callback_cannot_advance_new_request(self):
        monitor=NativeMonitor(api())
        for phase in ('submit','accepted','callback_success'):
            monitor.feed(operation='io',instance='one',phase=phase,buffer='buffer',returned_buffer='buffer')
        monitor.feed(operation='io',instance='two',phase='submit',buffer='buffer')
        monitor.feed(operation='io',instance='two',phase='accepted')
        with self.assertRaises(ContractError): monitor.feed(operation='io',instance='one',phase='callback_success',returned_buffer='buffer')
        self.assertEqual(monitor.result()['pending_requests'],1)


class FSMTests(unittest.TestCase):
    def test_failed_io_does_not_advance_sequence(self):
        machine=Interpreter(contract("Lifecycle(Sequence(),Sequence(WriteBytes([1],name='write',boundary='stop'),ReadFrame('f',name='read',boundary='stop')),on_failure=FailCurrentOperation(True))",
                                     frames="{'f':FrameLayout(1,{'v':Field(0,8,'big')})}"))
        for op,phase,t in [('initialize','logical_success',0),('acquire','logical_success',1),('write','physical_start',2),('write','physical_failure',3)]:
            machine.feed(InputEvent(op,phase,'a',t))
        self.assertEqual(machine.result()['state'],'failure')
        with self.assertRaises(ContractError): machine.feed(InputEvent('read','physical_start','a',4))

    def test_actual_wait_anchor_and_frame_commit(self):
        behavior="Lifecycle(Sequence(),Sequence(WriteBytes([1],name='write',boundary='stop'),WaitAfterCompletion('write','5 ms'),ReadFrame('f',name='read',boundary='stop'),CommitValidatedSample('f')),on_failure=FailCurrentOperation(True))"
        def prepared():
            machine=Interpreter(contract(behavior,frames="{'f':FrameLayout(1,{'v':Field(0,8,'big')})}"))
            for op,phase,t in [('initialize','logical_success',0),('acquire','logical_success',0),('write','physical_start',0),('write','physical_success',Fraction(1,1000))]:
                machine.feed(InputEvent(op,phase,'a',t))
            return machine
        machine=prepared()
        wait='behavior.acquisition.steps.1'
        with self.assertRaises(ContractError): machine.feed(InputEvent(wait,'logical_success','a',Fraction(5,1000)))
        machine=prepared();machine.feed(InputEvent(wait,'logical_success','a',Fraction(6,1000)))
        machine.feed(InputEvent('read','physical_start','a',Fraction(6,1000)))
        machine.feed(InputEvent('read','physical_success','a',Fraction(7,1000),b'\x09'))
        self.assertEqual(machine.sample['f'].fields['v'],9)

    def test_explicit_transition_action_finishes_before_target(self):
        machine=Interpreter(contract("FSM('a',[State('a'),State('b')],[Transition('a','request','b',action=WriteBytes([9],name='io',boundary='stop'))],accepting=['b'])"))
        machine.feed(InputEvent('request','logical_success','a',0))
        self.assertNotEqual(machine.state,'success')
        machine.feed(InputEvent('io','physical_start','a',1))
        machine.feed(InputEvent('io','physical_success','a',2))
        self.assertEqual(machine.state,'success')

    def test_parallel_assignments_read_previous_values(self):
        machine=Interpreter(contract("FSM('a',[State('a'),State('b')],[Transition('a','swap','b',effects=[Assign('x','y'),Assign('y','x')])],variables={'x':{'type':'int','initial':1},'y':{'type':'int','initial':2}},accepting=['b'])"))
        machine.feed(InputEvent('swap','logical_success','a',0))
        self.assertEqual((machine.variables['x'],machine.variables['y']),(2,1))

    def test_choice_and_bounded_repeat(self):
        behavior="FSM('a',[State('a'),State('b')],[Transition('a','go','b',action=Repeat(WriteBytes([1],name='poll',boundary='stop'),until=False,maximum_attempts=2))],accepting=['b'])"
        machine=Interpreter(contract(behavior));machine.feed(InputEvent('go','logical_success','a',0))
        for i in range(2):
            machine.feed(InputEvent('poll','physical_start','a',i*2+1));machine.feed(InputEvent('poll','physical_success','a',i*2+2))
        self.assertEqual(machine.state,'failure')

    def test_unknown_failure_never_turns_into_success(self):
        machine=Interpreter(contract("Lifecycle(Sequence(),WriteBytes([1],name='io',boundary='stop'))"))
        for op,phase,t in [('initialize','logical_success',0),('acquire','logical_success',1),('io','physical_start',2),('io','physical_failure',3)]:
            machine.feed(InputEvent(op,phase,'a',t))
        self.assertEqual(machine.result()['safety'],'unknown')
        self.assertFalse(machine.result()['operation_succeeded'])


if __name__ == '__main__': unittest.main()
