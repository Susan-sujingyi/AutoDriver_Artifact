"""Synthetic projection counterexamples; these are not candidate executions."""
import json
import unittest

from tests.regression.test_autodriver_stimuli import model
from method.autodriver.implementation.system.contract.stimuli import build_frame
from method.autodriver.implementation.system.native.plan import Projection,split_cases,numeric_point
from method.autodriver.implementation.system.contract.model import ContractError,node
from method.autodriver.implementation.system.contract.data import decode_frame


class NativeProjectionTests(unittest.TestCase):
    def run_trace(self,*,read_tick=5,command=8,corrupt=False,early_publish=False):
        contract=model("Lifecycle(WriteBytes([9],name='setup'),Sequence(WriteBytes([8],name='measure'),WaitAfterCompletion('measure','3 ms'),ReadFrame('f',name='read'),CommitValidatedSample('f')),on_failure=FailCurrentOperation(True),recovery=None,reentry=None)")
        projection=Projection(contract)
        def feed(phase,tick,**fields):projection.feed(dict(phase=phase,tick=tick,frequency=1000,**fields))
        def io(id,write,read,start,data=()):
            feed('submit',start,operation=id,write=write,read_len=read)
            feed('physical_start',start,operation=id)
            feed('physical_success',start+1,operation=id)
            feed('callback_success',start+1,operation=id,data=list(data))
        io(1,[9],0,0)
        feed('request_boundary',1)
        io(2,[command],0,1)
        if early_publish:feed('output',2,failed=False,value=2,channel='temperature')
        payload=bytearray(build_frame(contract['frames']['f'],{'a':0,'b':1}))
        if corrupt:payload[-1]^=1
        io(3,[],4,read_tick,payload)
        feed('output',read_tick+1,failed=corrupt,value=2,channel='temperature')
        return projection.result()

    def test_physical_completion_wait_and_actual_write_bytes(self):
        self.assertEqual(self.run_trace()['status'],'satisfied')
        for options in ({'read_tick':4},{'command':7},{'early_publish':True}):
            with self.subTest(options=options):self.assertEqual(self.run_trace(**options)['status'],'violated')

    def test_corrupt_actual_frame_takes_failure_without_public_success(self):
        report=self.run_trace(corrupt=True)
        self.assertEqual(report['status'],'satisfied');self.assertFalse(report['fsm']['operation_succeeded'])

    def test_missing_or_ambiguous_case_boundaries_are_rejected(self):
        for events in ([{'phase':'output'}],[{'phase':'case_begin','case':'x'}],
                       [{'phase':'case_begin','case':'x'},{'phase':'case_begin','case':'y'}]):
            with self.subTest(events=events),self.assertRaises(ContractError):split_cases('\n'.join(map(json.dumps,events)))

    def test_numeric_point_uses_current_formula_scale_and_rounding(self):
        from types import SimpleNamespace
        from fractions import Fraction
        dbc=model('Lifecycle(Sequence(),Sequence())');fields=dict(dbc.fields)
        fields['quantities']={'pressure':node('PhysicalQuantity',expression='f.b / 3',unit='Pa')};dbc=node('DeviceContract',**fields)
        encoding=node('OutputEncoding',storage_type='i32',unit='Pa',scale=Fraction(1,10),offset=0)
        fp=node('FrameworkProfile',outputs={'pressure':node('SensorInterface',encoding=encoding)})
        bp=node('BindingPlan',obligations=(node('NumericEncoding',physical_relation=node('FromDBC',path='dbc.quantities.pressure'),
            encoding=node('FromFP',path='fp.outputs.pressure.encoding'),rounding=node('Rounding',mode='nearest_ties_even'),error_bound=node('Unspecified')),))
        data=build_frame(dbc['frames']['f'],{'a':0,'b':1});frame=decode_frame(dbc['frames']['f'],data,name='f',instance='one')
        p=SimpleNamespace(machine=SimpleNamespace(sample={'f':frame},frames={'f':frame},instance='one',variables={}))
        report=numeric_point(dbc,fp,bp,p,{'channel':'pressure','value':3})
        self.assertEqual(report['encoding_value_status'],'satisfied');self.assertEqual(report['precision_requirement_status'],'unknown')
        with self.assertRaisesRegex(ContractError,'native numeric output differs'):numeric_point(dbc,fp,bp,p,{'channel':'pressure','value':4})
        # Later scratch-frame writes cannot change the already committed value.
        later=decode_frame(dbc['frames']['f'],build_frame(dbc['frames']['f'],{'a':0,'b':2}),name='f',instance='one')
        p.machine.frames={'f':later}
        self.assertEqual(numeric_point(dbc,fp,bp,p,{'channel':'pressure','value':3})['stored'],3)
        fields=dict(dbc.fields);fields['quantities']={'pressure':node('PhysicalQuantity',expression='f.b / 3',unit='Pa',requires=('valid',))};dbc=node('DeviceContract',**fields)
        p.machine.sample_variables={'valid':True};p.machine.variables={'valid':False}
        with self.assertRaisesRegex(ContractError,'validity requirement'):numeric_point(dbc,fp,bp,p,{'channel':'pressure','value':3})
        p.machine.sample_variables={'valid':False};p.machine.variables={'valid':True}
        with self.assertRaisesRegex(ContractError,'validity requirement'):numeric_point(dbc,fp,bp,p,{'channel':'pressure','value':3})
        p.machine.instance='new_acquisition'
        with self.assertRaisesRegex(ContractError,'old or unvalidated'):numeric_point(dbc,fp,bp,p,{'channel':'pressure','value':3})


if __name__=='__main__':unittest.main()
