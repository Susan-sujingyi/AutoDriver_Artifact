"""Explicit bounded integer operations agree in interpreter and solver."""
from fractions import Fraction
import unittest

from method.autodriver.implementation.system.contract.expression import scalar
from method.autodriver.implementation.system.contract.model import parse,node,Unknown,ContractError
from method.autodriver.implementation.system.contract.solver import prove,solve_witness
from method.autodriver.implementation.system.contract.relations import Relations
from method.autodriver.implementation.system.contract.domains import numeric_domain
from method.autodriver.implementation.system.contract.check import check_contract


class BitRelationTests(unittest.TestCase):
    def test_guard_widths_do_not_guess_promotion_or_wider_constants(self):
        from method.autodriver.implementation.system.contract.expression import bit_guard_width
        self.assertEqual(bit_guard_width('(status & 9)==0',{'status':8}),8)
        for text in ('(status & 256)==0','(status<<1)==0','(status^255)==0','(status & ~9)==0',
                     '(status>>8)==0','((status+1)&9)==0'):
            with self.subTest(text=text),self.assertRaises(Unknown):bit_guard_width(text,{'status':8})
        with self.assertRaises(Unknown):bit_guard_width('(a&1)==(b&1)',{'a':8,'b':16})
        with self.assertRaises(Unknown):bit_guard_width(node('Unspecified',reason='missing'),{})

    def test_masks_signed_shift_complement_and_explicit_decoding(self):
        variables={'raw':'int'};bounds=['raw>=0','raw<=255']
        for condition in ('(raw & 9)==(raw%2 + ((raw//8)%2)*8)',
                          '(~raw)==255-raw','extract(raw,4,4)==raw//16',
                          '(raw<128 and signed(raw,8)==raw) or (raw>=128 and signed(raw,8)==raw-256)'):
            result=prove(condition,variables,bounds,bit_width=8)
            self.assertEqual(result['status'],'satisfied',result)
        result=prove('(raw>>3)==raw//8',variables,['raw>=-128','raw<=127'],bit_width=8)
        self.assertEqual(result['status'],'satisfied',result)
        for value in (-128,-1,0,1,127,128,255):
            for expression in ('raw & 9','raw | 9','raw ^ 9','raw >> 3','~raw','signed(unsigned(raw,8),8)'):
                try:expected=scalar(expression,{'raw':value},bit_width=8)
                except ContractError:
                    result=prove('('+expression+')==0',variables,['raw=='+str(value)],bit_width=8)
                    self.assertEqual(result['status'],'violated');continue
                result=prove('('+expression+')=='+str(expected),variables,['raw=='+str(value)],bit_width=8)
                self.assertEqual(result['status'],'satisfied',(value,expression,result))

    def test_overflow_missing_width_and_fractional_operands_never_pass(self):
        self.assertEqual(prove('raw & 9 == 0',{'raw':'int'},['raw>=0','raw<=255'])['status'],'unknown')
        for expression,value in [('raw<<1',128),('raw^255',-1),('raw&255',256),('raw>>8',1),('raw&1',Fraction(1,2))]:
            with self.subTest(expression=expression,value=value):
                with self.assertRaises(ContractError):scalar(expression,{'raw':value},bit_width=8)
                try:result=prove('('+expression+')==0',{'raw':'real'},['raw=='+str(value)],bit_width=8)
                except ContractError:continue  # Invalid literal shift is structural.
                self.assertEqual(result['status'],'violated',result)
        witness=solve_witness('(raw & 9)==0',{'raw':'int'},['raw>=1','raw<=255'],bit_width=8)
        self.assertEqual(witness['status'],'satisfied')
        value=int(witness['assignment']['raw']);self.assertEqual(value&9,0)

    def test_named_relations_own_their_widths_and_old_calls_remain_readable(self):
        def relation(text,width=None,unit='1'):
            return node('PhysicalQuantity',expression=text,unit=unit,bit_width=width)
        model=Relations({'masked':relation('frame.raw & 15',8),'output':relation('(masked<<1)/2',16,'Pa')})
        domain={'frame.raw':{'type':'int','bounds':(0,255)}}
        result=numeric_domain(model.definitions['output'],domain,definitions=model.solver_definitions('output'),
            definition_widths=model.solver_widths('output'))
        self.assertEqual(result['status'],'satisfied',result);self.assertEqual(model.value('output',{'frame.raw':255}),15)
        missing=Relations({'masked':relation('frame.raw & 15'),'output':relation('masked',16,'Pa')})
        with self.assertRaises(Unknown):missing.value('output',{'frame.raw':255})
        result=numeric_domain(missing.definitions['output'],domain,definitions=missing.solver_definitions('output'),
            definition_widths=missing.solver_widths('output'))
        self.assertEqual(result['status'],'unknown')
        for width in (None,8):
            text="DeviceContract('synthetic',I2C(1),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),frames={'f':FrameLayout(1,{'raw':Field(0,8,'big')})},quantities={'value':PhysicalQuantity('f.raw & 15','Pa'"+('' if width is None else ',bit_width=8')+")})"
            root,report=check_contract(text,'dbc')
            self.assertEqual(root['quantities']['value']['bit_width'],width)
            finding=next(f for f in report['findings'] if f['check']=='numeric_definedness_over_declared_bounds')
            self.assertEqual(finding['status'],'unknown' if width is None else 'satisfied')
