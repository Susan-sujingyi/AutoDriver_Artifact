"""Named compensation algebra without sensor constants or private oracles."""
from fractions import Fraction
import unittest

from method.autodriver.implementation.system.contract.relations import Relations
from method.autodriver.implementation.system.contract.model import node,Unknown,ContractError
from method.autodriver.implementation.system.contract.check import check_contract


def relation(expression,unit='1'):return node('PhysicalQuantity',expression=expression,unit=unit)


class RelationTests(unittest.TestCase):
    def test_opaque_source_calls_cannot_stand_in_for_normative_relations(self):
        from method.autodriver.implementation.system.contract.expression import check_pure_expression
        for expression in ('source_compensate(raw, gain)','raw.decode()',"__import__('os')",'raw[0]','sum([raw])','lambda: raw'):
            with self.subTest(expression=expression),self.assertRaises(ContractError):check_pure_expression(expression)
        for expression in ('(raw+gain)/10','max(0, min(raw,255))','signed(raw,16)','sqrt(raw)','raw & 9',
                           'raw >= 0 and not invalid'):
            check_pure_expression(expression)
        _,report=check_contract("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),quantities={'value':PhysicalQuantity('source_compensate(raw)','Pa')})",'dbc')
        self.assertTrue(any(f['check']=='physical_expression_syntax' and f['status']=='violated' for f in report['findings']))

    def test_requires_and_domain_are_executed_not_ignored(self):
        from method.autodriver.implementation.system.contract.data import DataInvalid
        model=Relations({'helper':node('PhysicalQuantity',expression='raw+1',unit='1',requires=('valid',)),
            'value':node('PhysicalQuantity',expression='helper/2',unit='Pa',requires=(),domain='raw >= 1')})
        self.assertEqual(model.value('value',{'raw':1,'valid':True}),1)
        with self.assertRaises(DataInvalid):model.value('value',{'raw':1,'valid':False})
        with self.assertRaises(DataInvalid):model.value('value',{'raw':0,'valid':True})
        with self.assertRaises(DataInvalid):model.value('value',{'raw':1,'valid':True},validity_environment={'raw':1,'valid':False})
        prose=Relations({'value':node('PhysicalQuantity',expression='raw',unit='1',requires=('data is current',))})
        with self.assertRaises(Unknown):prose.value('value',{'raw':2})
        bounded=Relations({'value':node('PhysicalQuantity',expression='raw',unit='1',requires=(),domain={'raw':{'type':'int','bounds':(1,3)}})})
        with self.assertRaises(DataInvalid):bounded.value('value',{'raw':4})

    def test_dependency_order_qualified_names_and_exact_scalars(self):
        model=Relations({'value':relation('quantities.adjusted / 10','Pa'),
            'adjusted':relation('gain * (raw + offset)'),'gain':relation('2 / 3')})
        self.assertEqual(model.value('value',{'raw':8,'offset':1}),Fraction(3,5))
        text=model.expanded('value');self.assertNotIn('adjusted',text);self.assertNotIn('gain',text)
        from method.autodriver.implementation.system.contract.expression import scalar
        self.assertEqual(scalar(text,{'raw':8,'offset':1}),Fraction(3,5))
        self.assertEqual(Relations({'display value':relation('raw + 1')}).value('display value',{'raw':2}),3)

    def test_cycles_missing_names_units_and_ambiguous_state_do_not_get_values(self):
        with self.assertRaises(ContractError):Relations({'a':relation('b+1'),'b':relation('a+1')})
        with self.assertRaises(Unknown):Relations({'a':relation('quantities.missing')})
        model=Relations({'a':relation('b+1'),'b':relation('raw')})
        with self.assertRaises(Unknown):model.value('a',{})
        with self.assertRaises(Unknown):model.value('a',{'b':2,'raw':1})
        model=Relations({'a':relation('b+1','Pa'),'b':relation('raw','degC')})
        with self.assertRaises(Unknown):model.value('a',{'raw':1})
        alias=Relations({'a':relation('quantities.b','Pa'),'b':relation('raw','Pa')})
        self.assertEqual(alias.value('a',{'raw':7}),7)

    def test_current_contract_checker_detects_cycles_and_expands_supported_algebra(self):
        _,report=check_contract("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),quantities={'a':PhysicalQuantity('b+1','1'),'b':PhysicalQuantity('a','1')})",'dbc')
        self.assertTrue(any(f['check']=='named_relation_dependencies' and f['status']=='violated' for f in report['findings']))
        _,report=check_contract("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),frames={'f':FrameLayout(1,{'x':Field(0,8,'big')})},quantities={'gain':PhysicalQuantity('f.x*3','1'),'pressure':PhysicalQuantity('gain/2','Pa')})",'dbc')
        checks=[f for f in report['findings'] if f['check']=='numeric_definedness_over_declared_bounds']
        self.assertEqual(len(checks),2);self.assertTrue(all(f['status']=='satisfied' for f in checks))
        _,report=check_contract("DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),frames={'f':FrameLayout(1,{'x':Field(0,8,'big')})},quantities={'x':PhysicalQuantity('x/2','Pa')})",'dbc')
        graph=next(f for f in report['findings'] if f['check']=='named_relation_dependencies')
        self.assertEqual(graph['status'],'unknown');self.assertIn('ambiguous',graph['detail'])

    def test_large_expansion_is_explicitly_unknown_not_truncated(self):
        values={'v0':relation('raw')}
        for i in range(1,20):values['v'+str(i)]=relation('v'+str(i-1)+' + v'+str(i-1))
        model=Relations(values);self.assertEqual(model.value('v19',{'raw':1}),1<<19)
        with self.assertRaises(Unknown):model.expanded('v19')
        from method.autodriver.implementation.system.contract.domains import numeric_domain
        checked=numeric_domain(values['v19'],{'raw':{'type':'int','bounds':(0,255)}},definitions=model.solver_definitions('v19'))
        self.assertEqual(checked['status'],'satisfied')
        self.assertEqual(len(model.solver_definitions('v19')),19)

    def test_solver_shared_definitions_preserve_poles_aliases_and_unit_checks(self):
        from method.autodriver.implementation.system.contract.domains import numeric_domain
        from method.autodriver.implementation.system.contract.solver import prove
        values={'gain':relation('raw-13'),'square':relation('quantities.gain * gain'),
                'output':relation('1 / square','Pa')}
        model=Relations(values)
        checked=numeric_domain(values['output'],{'raw':{'type':'int','bounds':(0,255)}},definitions=model.solver_definitions('output'))
        self.assertEqual(checked['status'],'violated');self.assertEqual(checked['counterexample']['raw'],'13')
        self.assertEqual(prove('term==1',{},definitions={'term':'not_declared'})['status'],'unknown')
        with self.assertRaises(ContractError):prove('a==1',{},definitions={'a':'b+1','b':'a+1'})
        with self.assertRaises(Unknown):prove('a==1',{'a':'int'},definitions={'a':'1'})
        model=Relations({'a':relation('b+1','Pa'),'b':relation('raw','degC')})
        with self.assertRaises(Unknown):model.solver_definitions('a')

    def test_native_point_uses_named_definitions_and_separates_bad_formula_from_bad_output(self):
        from types import SimpleNamespace
        from tests.regression.test_autodriver_stimuli import model
        from method.autodriver.implementation.system.contract.stimuli import build_frame
        from method.autodriver.implementation.system.contract.data import decode_frame
        from method.autodriver.implementation.system.native.plan import numeric_point
        original=model('Lifecycle(Sequence(),Sequence())')
        def contract(expression):return node('DeviceContract',**dict(original.fields,quantities={'gain':relation('f.b*2'),'pressure':relation(expression,'Pa')}))
        fp=node('FrameworkProfile',outputs={'pressure':node('SensorInterface',encoding=node('OutputEncoding',storage_type='i32',unit='Pa',scale=Fraction(1,10),offset=0))})
        bp=node('BindingPlan',obligations=[node('NumericEncoding',physical_relation=node('FromDBC',path='dbc.quantities.pressure'),encoding=node('FromFP',path='fp.outputs.pressure.encoding'),rounding=node('Rounding',mode='nearest_ties_even'),error_bound=node('Unspecified'))])
        frame=decode_frame(original['frames']['f'],build_frame(original['frames']['f'],{'a':0,'b':1}),name='f',instance='current')
        projection=SimpleNamespace(machine=SimpleNamespace(sample={'f':frame},frames={'f':frame},instance='current',variables={}))
        event={'channel':'pressure','value':10}
        result=numeric_point(contract('(gain+1)/3'),fp,bp,projection,event)
        self.assertEqual(result['encoding_value_status'],'satisfied');self.assertEqual(result['precision_requirement_status'],'unknown')
        with self.assertRaises(ContractError):numeric_point(contract('(gain+1)/3'),fp,bp,projection,dict(event,value=11))
        with self.assertRaisesRegex(Unknown,'DBC numerical relation'):numeric_point(contract('gain/0'),fp,bp,projection,event)
