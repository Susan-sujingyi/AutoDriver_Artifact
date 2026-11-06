"""Exact floating encoding counterexamples, including binary64 double rounding."""
from fractions import Fraction
import unittest

from method.autodriver.implementation.system.contract.data import round_float,encode,power_two,float_bits_value,DataInvalid
from method.autodriver.implementation.system.contract.model import node,ContractError


class FloatEncodingTests(unittest.TestCase):
    def test_direct_rational_to_binary32_avoids_double_rounding(self):
        value=1+power_two(-24)+power_two(-80)
        self.assertEqual(round_float(value,'f32','nearest_ties_even')['stored_bits'],0x3f800001)
        detour=Fraction(float(value))
        self.assertEqual(round_float(detour,'f32','nearest_ties_even')['stored_bits'],0x3f800000)

    def test_subnormal_ties_directed_rounding_and_signed_zero(self):
        self.assertEqual(round_float(power_two(-150),'f32','nearest_ties_even')['stored_bits'],0)
        self.assertEqual(round_float(3*power_two(-150),'f32','nearest_ties_even')['stored_bits'],2)
        self.assertEqual(round_float(-power_two(-150),'f32','floor')['stored_bits'],0x80000001)
        self.assertEqual(round_float(-power_two(-150),'f32','toward_zero')['stored_bits'],0x80000000)
        self.assertEqual(round_float(power_two(-1074),'f64','nearest_ties_even')['stored_bits'],1)
        self.assertEqual(float_bits_value(0x3eaaaaab,'f32'),Fraction(11184811,33554432))
        with self.assertRaises(DataInvalid):float_bits_value(0x7fc00000,'f32')

    def test_finite_overflow_and_physical_error_bound_remain_checked(self):
        maximum=(2-power_two(-23))*power_two(127)
        self.assertEqual(round_float(maximum,'f32','nearest_ties_even')['stored_bits'],0x7f7fffff)
        with self.assertRaises(ContractError):round_float(power_two(128),'f32','nearest_ties_even')
        encoding=node('OutputEncoding',storage_type='f32',unit='percent_RH',scale=1,offset=0,range=(0,100))
        rounding=node('Rounding',mode='nearest_ties_even',overflow='report_gap')
        report=encode(Fraction(1,3),'%RH',encoding,rounding,Fraction(1,10**7))
        self.assertEqual(report['stored_bits'],0x3eaaaaab)
        self.assertEqual(report['physical_error'],abs(report['exact_stored']-Fraction(1,3)))
        with self.assertRaises(ContractError):encode(Fraction(1,3),'%RH',encoding,rounding,0)


if __name__=='__main__':unittest.main()
