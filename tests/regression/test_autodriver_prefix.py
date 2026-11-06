"""Graph-derived frame witnesses and explicit numeric binding, synthetic only."""
import unittest
from fractions import Fraction
from types import SimpleNamespace

from method.autodriver.implementation.system.contract.model import parse,Unknown,node
from method.autodriver.implementation.system.contract.data import decode_frame,frame_environment
from method.autodriver.implementation.system.native.prefix import success_prefix,wire_response
from method.autodriver.implementation.system.native.plan import numeric_point


def contract(guard='id.code == 17'):
    return parse("DeviceContract('synthetic',SPI(0,'selected'),FrozenPolicy(),{},Lifecycle(Sequence(Transfer([WriteSegment([3]),ReadSegment(1,'id')],'end'),Choice(Case("+repr(guard)+",WriteBytes([9])),otherwise=FailCurrentOperation(True))),Sequence(Transfer([WriteSegment([5]),ReadSegment(1,'sample')],'end'),CommitValidatedSample('sample')),recovery=None,reentry=None),frames={'id':FrameLayout(1,{'code':Field(0,5,'big'),'revision':Field(5,3,'big')}),'sample':FrameLayout(1,{'raw':Field(0,8,'big')})})",'dbc')


class PrefixTests(unittest.TestCase):
    def test_guarded_init_value_comes_from_current_graph_not_boundary_table(self):
        root=contract();prefix=success_prefix(root,'spi')
        frame=decode_frame(root['frames']['id'],prefix['frames']['id'],name='id',instance='one')
        self.assertEqual(frame.fields['code'],17)
        self.assertEqual([s['phase'] for s in prefix['steps']],['initialization','initialization','acquisition'])
        self.assertEqual(wire_response(prefix['steps'][0],prefix['frames'])[1]>>3,17)
        self.assertEqual(prefix['steps'][0]['wire'].write,(3,None))

    def test_unsatisfiable_or_unencoded_guard_does_not_create_a_success_witness(self):
        for guard in ['id.code == 32','id has been accepted']:
            with self.subTest(guard=guard),self.assertRaises(Unknown):success_prefix(contract(guard),'spi')

    def test_delivery_binding_does_not_assume_physical_quantity_name_matches_channel(self):
        root=contract();fields=dict(root.fields);fields['quantities']={'physical_value':node('PhysicalQuantity',expression='sample.raw / 4',unit='degC')};root=node('DeviceContract',**fields)
        fp=parse("FrameworkProfile('synthetic','snapshot',outputs={'temperature':SensorInterface('read',encoding=OutputEncoding('i32','degC',scale=0.5))})",'fp')
        bp=parse("BindingPlan(Ref('d'),Ref('f'),Ref('s'),'synthetic',primitive_bindings={'delivery':DeliverRequestedQuantity(FromDBC('quantities.physical_value'),FromFP('outputs.temperature.encoding'),Rounding('nearest_ties_even'))})",'bp')
        frame=decode_frame(root['frames']['sample'],[5],name='sample',instance='one')
        projection=SimpleNamespace(machine=SimpleNamespace(sample={'sample':frame},frames={'sample':frame},instance='one',variables={}))
        point=numeric_point(root,fp,bp,projection,{'channel':'temperature','value':2})
        self.assertEqual(point['encoding_value_status'],'satisfied');self.assertEqual(point['precision_requirement_status'],'unknown')

    def test_integer_tie_inputs_respect_an_executable_domain_or_remain_unknown(self):
        from method.autodriver.implementation.system.native.numeric_stimuli import integer_ties
        root=contract();fields=dict(root.fields)
        fields['quantities']={'q':node('PhysicalQuantity',expression='sample.raw / 4',unit='degC',domain='0 <= sample.raw <= 255')};root=node('DeviceContract',**fields)
        fp=parse("FrameworkProfile('synthetic','snapshot',outputs={'temperature':SensorInterface('read',encoding=OutputEncoding('i32','degC',scale=0.5))})",'fp')
        bp=parse("BindingPlan(Ref('d'),Ref('f'),Ref('s'),'synthetic',primitive_bindings={'delivery':DeliverRequestedQuantity(FromDBC('quantities.q'),FromFP('outputs.temperature.encoding'),Rounding('nearest_ties_even'))})",'bp')
        values=integer_ties(root,fp,bp,{'sample'});self.assertEqual(len(values['cases']),2)
        self.assertEqual({case['frames']['sample'][0]%4 for case in values['cases']},{1,3})
        fields['quantities']={'q':node('PhysicalQuantity',expression='sample.raw / 4',unit='degC',domain='physical value is valid')};root=node('DeviceContract',**fields)
        unknown=integer_ties(root,fp,bp,{'sample'});self.assertEqual(unknown['cases'],[]);self.assertTrue(unknown['gaps'])

    def test_rounding_witnesses_use_named_widths_and_exclude_undefined_inputs(self):
        from method.autodriver.implementation.system.native.numeric_stimuli import integer_ties
        root=contract();fields=dict(root.fields)
        fp=parse("FrameworkProfile('synthetic','snapshot',outputs={'temperature':SensorInterface('read',encoding=OutputEncoding('i32','degC',scale=0.5))})",'fp')
        bp=parse("BindingPlan(Ref('d'),Ref('f'),Ref('s'),'synthetic',primitive_bindings={'delivery':DeliverRequestedQuantity(FromDBC('quantities.q'),FromFP('outputs.temperature.encoding'),Rounding('nearest_ties_even'))})",'bp')
        fields['quantities']={'masked':node('PhysicalQuantity',expression='sample.raw & 15',unit='1',bit_width=8),
            'q':node('PhysicalQuantity',expression='masked/4',unit='degC',domain=None)}
        result=integer_ties(node('DeviceContract',**fields),fp,bp,{'sample'})
        self.assertEqual(len(result['cases']),2);self.assertEqual(result['gaps'],[])
        fields['quantities']={'q':node('PhysicalQuantity',expression='1/(sample.raw-13)',unit='degC',domain=None,requires=('sample.raw != 13',))}
        result=integer_ties(node('DeviceContract',**fields),fp,bp,{'sample'})
        self.assertEqual(len(result['cases']),2,result)
        self.assertTrue(all(case['frames']['sample'][0]!=13 for case in result['cases']))
