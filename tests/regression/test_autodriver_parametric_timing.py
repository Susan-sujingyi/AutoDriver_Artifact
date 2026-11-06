"""Generic clock arithmetic; no sensor instance or hardware assertions."""
import unittest
import json

from method.autodriver.implementation.system.contract.timing import check_parametric_tick_choice
from method.autodriver.implementation.system.contract.model import Unknown,parse,identity


class ParametricTimingTests(unittest.TestCase):
    def test_binding_keeps_provider_unknown_after_parametric_arithmetic_success(self):
        from method.autodriver.implementation.system.contract.binding import check_binding
        dbc=parse("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(WriteBytes([1],name='send'),WaitAfterCompletion('send','1.5 ms')))",'dbc')
        fp=parse("FrameworkProfile('synthetic','frozen',capabilities={'send':APIContract('send',{},completion=Callback('done',{}))})",'fp')
        support={}
        choice="CheckedPhysicalLowerBound(ticks='max(1,(3*hz+1999)//2000)',phase_loss=0,variables={'hz':{'type':'int','bounds':(1,1000000)}},clock_bounds={'frequency_max':'hz','minimum_ticks':1,'maximum_ticks':4294967295,'reference_not_before_anchor':True,'callback_not_before_deadline':True})"
        bp=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',primitive_bindings={'WriteBytes':BindAsyncCall(FromFP('capabilities.send'),FromFP('capabilities.send.completion')),'WaitAfterCompletion':BindAlarm(EventRef('send','physical_success'),FromDBC('behavior.acquisition'),"+choice+",'after deadline')})",'bp')
        report=check_binding(dbc,fp,bp,support,parent_hashes={'dbc':identity(dbc),'fp':identity(fp),'support':identity(support)})
        proof=next(iter(report['tick_proofs'].values()));self.assertEqual(proof['status'],'satisfied')
        self.assertEqual(proof['provider_assurance'],'unknown');self.assertEqual(report['status'],'unknown')
        self.assertTrue(any(f['check']=='timing_provider_evidence' and f['status']=='unknown' for f in report['findings']))
        self.assertEqual(json.loads(json.dumps(report))['tick_proofs'],report['tick_proofs'])

    def check(self,ticks,*,phase=0,maximum=4294967295,assuming=(),domain=(1,1000000)):
        return check_parametric_tick_choice('1.5 ms',ticks,'hz',phase,minimum_ticks=1,maximum_ticks=maximum,
            variables={'hz':{'type':'int','bounds':domain}},assuming=assuming,
            reference_not_before_anchor=True,callback_not_before_deadline=True)

    def test_ceiling_is_checked_over_all_declared_frequencies(self):
        good=self.check('max(1,(3*hz+1999)//2000)')
        self.assertEqual(good['status'],'satisfied');self.assertEqual(good['provider_assurance'],'unknown')
        self.assertEqual(good['candidate_guard_conformance'],'not_checked')
        bad=self.check('max(1,(3*hz)//2000)')
        self.assertEqual(bad['status'],'violated');self.assertIn('hz',bad['counterexample'])

    def test_phase_counter_range_non_integer_and_zero_frequency_counterexamples(self):
        self.assertEqual(self.check('max(1,(3*hz+1999)//2000)',phase=1)['status'],'violated')
        self.assertEqual(self.check('max(1,(3*hz+1999)//2000+1)',phase=1)['status'],'satisfied')
        self.assertEqual(self.check('max(1,(3*hz+1999)//2000)',maximum=10)['status'],'violated')
        guarded=self.check('max(1,(3*hz+1999)//2000)',maximum=10,assuming=['max(1,(3*hz+1999)//2000)<=10'])
        self.assertEqual(guarded['status'],'satisfied');self.assertTrue(guarded['assuming'])
        self.assertEqual(self.check('3*hz/2000+1')['status'],'violated')
        self.assertEqual(self.check('max(1,(3*hz+1999)//2000)',domain=(0,1000))['status'],'violated')

    def test_false_premises_and_unencoded_expressions_do_not_pass(self):
        self.assertEqual(self.check('max(1,(3*hz+1999)//2000)',assuming=['hz<0'])['status'],'unknown')
        self.assertEqual(self.check('undeclared(hz)')['status'],'unknown')
        with self.assertRaises(Unknown):
            check_parametric_tick_choice('1 ms','hz','hz',0,minimum_ticks=1,maximum_ticks=100,variables={},
                reference_not_before_anchor=False,callback_not_before_deadline=True)
