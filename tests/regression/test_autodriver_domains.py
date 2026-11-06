"""Declared-domain counterexamples, independent of sensors and scoring."""
from fractions import Fraction
import unittest

from method.autodriver.implementation.system.contract.model import node,Unknown,ContractError,parse
from method.autodriver.implementation.system.contract.domains import partition,select,minimum_wait,numeric_domain,domains_from_frames
from method.autodriver.implementation.system.contract.solver import prove


class DomainTests(unittest.TestCase):
    def test_integer_solver_optimization_preserves_exact_division_and_negative_floor(self):
        result=prove('7/2 == 3.5 and -7//2 == -4 and 7//-2 == -4 and -7%2 == 1 and 7%-2 == -1',{})
        self.assertEqual(result['status'],'satisfied')
        result=prove('a//-2 == -1',{'a':'int'},['a>=1','a<=2'])
        self.assertEqual(result['status'],'satisfied')

    def test_partition_finds_overlap_and_uncovered_boundary(self):
        domains={'v':{'type':'real','bounds':(0,2)}}
        self.assertEqual(partition({'v < 1':'a','v >= 1':'b'},domains)['status'],'satisfied')
        overlap=partition({'v <= 1':'a','v >= 1':'b'},domains)
        self.assertEqual(overlap['status'],'violated');self.assertEqual(overlap['checks']['disjoint']['counterexample']['v'],'1')
        self.assertEqual(partition({'v < 1':'a','v > 1':'b'},domains)['checks']['coverage']['status'],'violated')

    def test_unknown_environment_cannot_choose_faster_branch(self):
        value=node('ConditionalValue',cases={'v < 1':'2 ms','v >= 1':'5 ms'},otherwise=node('Unspecified',reason='no default'))
        with self.assertRaises(Unknown):select(value,{})
        result=minimum_wait(value,domains={'v':{'type':'real','bounds':(0,2)}})
        self.assertEqual(result['status'],'satisfied');self.assertEqual(result['seconds'],Fraction(5,1000))
        self.assertEqual(minimum_wait(value,environment={'v':0})['seconds'],Fraction(2,1000))
        self.assertEqual(minimum_wait(value,domains={})['status'],'unknown')

    def test_unknown_default_is_not_an_infinite_or_zero_time(self):
        value=node('ConditionalValue',cases={'v < 1':'2 ms'},otherwise=node('Unspecified',reason='missing range'))
        self.assertEqual(minimum_wait(value,domains={'v':{'type':'real','bounds':(0,2)}})['status'],'violated')
        with self.assertRaises(Unknown):select(value,{'v':2})

    def test_formula_domain_checks_detect_zero_without_sample_enumeration(self):
        q=lambda expression:node('PhysicalQuantity',expression=expression)
        domains={'raw':{'type':'int','bounds':(0,255)}}
        self.assertEqual(numeric_domain(q('raw/255'),domains)['status'],'satisfied')
        result=numeric_domain(q('1/(raw-129)'),domains)
        self.assertEqual(result['status'],'violated');self.assertEqual(result['counterexample']['raw'],'129')
        self.assertEqual(numeric_domain(q('missing/10'),domains)['status'],'unknown')
        with self.assertRaises(ContractError):numeric_domain(q('raw > 1'),domains)
        guarded=node('PhysicalQuantity',expression='1/raw',requires=('raw>0',))
        checked=numeric_domain(guarded,domains)
        self.assertEqual(checked['status'],'satisfied');self.assertEqual(checked['conditional_preconditions'],['raw>0'])

    def test_frame_domains_do_not_alias_same_named_fields_in_different_frames(self):
        dbc=parse("DeviceContract('synthetic',I2C(7),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),frames={'a':FrameLayout(1,{'raw':Field(0,8,'big')}),'b':FrameLayout(1,{'raw':Field(0,8,'big',signed=True)})})",'dbc')
        domains=domains_from_frames(dbc)
        self.assertNotIn('raw',domains);self.assertEqual(domains['a.raw']['bounds'],(0,255));self.assertEqual(domains['b.raw']['bounds'],(-128,127))

    def test_literal_powers_keep_exact_domains_and_detect_poles(self):
        q=lambda text:node('PhysicalQuantity',expression=text)
        domains={'raw':{'type':'int','bounds':(0,4095)}}
        self.assertEqual(numeric_domain(q('raw/(2**12-1)'),domains)['status'],'satisfied')
        self.assertEqual(numeric_domain(q('raw*2**-12'),domains)['status'],'satisfied')
        pole=numeric_domain(q('(raw-2048)**-2'),domains)
        self.assertEqual(pole['status'],'violated');self.assertEqual(pole['counterexample']['raw'],'2048')
        self.assertEqual(numeric_domain(q('raw**0.5'),domains)['status'],'unknown')
        self.assertEqual(numeric_domain(q('raw**65'),domains)['status'],'unknown')


if __name__=='__main__':unittest.main()
