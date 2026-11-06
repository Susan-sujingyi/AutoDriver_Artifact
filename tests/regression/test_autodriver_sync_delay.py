"""Recovered synchronous timing bounds; no OS guarantees are hard-coded."""
import unittest
from dataclasses import replace
from types import MappingProxyType

from method.autodriver.implementation.system.contract.model import parse,node,identity,ContractError,Unknown
from method.autodriver.implementation.system.contract.binding import check_binding
from method.autodriver.implementation.system.contract.delay import lower_bound


def change(value,**fields):return replace(value,fields=MappingProxyType({**value.fields,**fields}))


class SyncDelayTests(unittest.TestCase):
    def setUp(self):
        self.dbc=parse("DeviceContract('synthetic',I2C(7),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence(WriteBytes([3],name='start'),WaitAfterCompletion('start','1501 us'))))",'dbc')
        self.fp=parse("FrameworkProfile('synthetic',Ref('snapshot'),capabilities={'io':APIContract('transfer',{},completion=SyncReturn('result',physical_completion_order='device_not_after_native')),'delay':APIContract('sleep',{'value':'bounded integer'},completion=SyncReturn('result',delay=ElapsedTimeLowerBound('us',(0,65535),'1 us','result == 0')))})",'fp')
        self.wait=self.dbc['behavior']['acquisition']['steps'][1]
        self.binding=parse("BindingPlan(Ref('dbc'),Ref('fp'),Ref('support'),'synthetic',primitive_bindings={'WriteBytes':BindSyncCall(FromFP('capabilities.io')),'WaitAfterCompletion':BindSyncDelay(FromFP('capabilities.delay'),1502,FromFP('capabilities.io'),EventBinding(EventRef('start','physical_success'),EventRef('transfer','logical_success'),'device_not_after_native'),FromDBC('behavior.acquisition.steps.1.minimum'),'continue')})",'bp')

    def findings(self,*,fp=None,bp=None):
        fp=fp or self.fp;bp=bp or self.binding;support={}
        result=check_binding(self.dbc,fp,bp,support,parent_hashes={'dbc':identity(self.dbc),'fp':identity(fp),'support':identity(support)})
        return [item for item in result['findings'] if item['check']=='synchronous_delay_lower_bound']

    def changed_binding(self,**values):
        bindings=dict(self.binding['primitive_bindings']);bindings['WaitAfterCompletion']=change(bindings['WaitAfterCompletion'],**values)
        return change(self.binding,primitive_bindings=bindings)

    def test_exact_units_early_loss_and_parent_duration(self):
        self.assertEqual(self.findings()[0]['status'],'satisfied')
        for values in ({'argument':1501},{'argument':65536},{'duration':node('FromDBC',path='behavior.acquisition.steps.0.minimum')},
                       {'on_interruption':'continue'}):
            with self.subTest(values=values):self.assertEqual(self.findings(bp=self.changed_binding(**values))[0]['status'],'violated')
        result=check_binding(self.dbc,self.fp,self.binding,{},parent_hashes={'dbc':identity(self.dbc),'fp':identity(self.fp),'support':identity({})})
        proof=result['tick_proofs']['behavior.acquisition.steps.1']
        self.assertEqual(proof['lower_seconds'],'1501/1000000')
        self.assertEqual(proof['candidate_order_and_interruption'],'not_checked')

    def test_unknown_provider_and_unproved_anchors_do_not_pass(self):
        capabilities=dict(self.fp['capabilities']);capabilities['io']=change(capabilities['io'],completion=change(capabilities['io']['completion'],physical_completion_order=node('ProviderConformanceRequired',relation='device_not_after_native')))
        self.assertEqual(self.findings(fp=change(self.fp,capabilities=capabilities))[0]['status'],'unknown')
        original=self.binding['primitive_bindings']['WaitAfterCompletion']['anchor']
        for event in (node('EventRef',operation='start',phase='physical_success',instance='old'),
                      node('EventRef',operation='different',phase='physical_success',instance='current')):
            self.assertEqual(self.findings(bp=self.changed_binding(anchor=change(original,device_event=event)))[0]['status'],'violated')
        self.assertEqual(self.findings(bp=self.changed_binding(anchor=change(original,assumptions=('provider works',))))[0]['status'],'unknown')
        self.assertEqual(self.findings(bp=self.changed_binding(anchor=change(original,relation='equal')))[0]['status'],'violated')

    def test_delay_contract_retains_interrupt_and_range_gaps(self):
        guarantee=self.fp['capabilities']['delay']['completion']['delay']
        for values in ({'argument_unit':'tick'},{'argument_range':(2,1)}, {'success_condition':'True'},
                       {'success_condition':'result == arbitrary'}, {'early_loss':'-1 us'}):
            with self.subTest(values=values),self.assertRaises(ContractError):lower_bound(change(guarantee,**values))
        with self.assertRaises(Unknown):lower_bound(change(guarantee,interruption='success'))
        # Old ordinary synchronous returns remain valid without a delay claim.
        old=parse("FrameworkProfile('synthetic',Ref('snapshot'),capabilities={'ordinary':APIContract('ordinary',{},completion=SyncReturn('value'))})",'fp')
        self.assertIsNone(old['capabilities']['ordinary']['completion']['delay'])
