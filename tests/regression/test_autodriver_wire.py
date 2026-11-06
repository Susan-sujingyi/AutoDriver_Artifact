"""Synthetic wire/header/segment tests, never sensor or evaluator fixtures."""
from pathlib import Path
import unittest

from method.autodriver.implementation.system.contract.model import parse,ContractError,Unknown

from method.autodriver.implementation.system.contract.wire import transaction


def contract(operation):
    return parse("DeviceContract('synthetic',SPI(0,'selected'),FrozenPolicy(),{},Lifecycle(Sequence(),"+operation+"),frames={'data':FrameLayout(2,{'raw':Field(0,16,'big')})})",'dbc')


class WireTests(unittest.TestCase):
    def test_spi_transfer_keeps_header_and_discards_its_receive_bytes(self):
        root=contract("Transfer([WriteSegment([18]),ReadSegment(2,'data')],'CS end')")
        plan=transaction(root,root['behavior']['acquisition'],'spi')
        plan.check({'write':[18,9,8],'read_len':3})
        self.assertEqual(plan.project([99,1,2]),{'data':b'\x01\x02'})
        with self.assertRaises(ContractError):plan.check({'write':[19,9,8],'read_len':3})
        with self.assertRaises(ContractError):plan.check({'write':[18,9,8],'read_len':2})

    def test_register_headers_and_dummy_prefix_are_explicit(self):
        encoding="{'address_bits':8,'address_byte_order':'big','read_header':['address | 128'],'write_header':['address'],'dummy_read_bytes':1,'auto_increment':False}"
        root=contract("ReadRegister(5,16,'big','register',encoding="+encoding+")")
        for bus,write,read in [('spi',[133,0,0,0],4),('i2c',[133],3)]:
            plan=transaction(root,root['behavior']['acquisition'],bus);plan.check({'write':write,'read_len':read})
            self.assertEqual(plan.project(([90] if bus=='spi' else [])+[91,1,2]),b'\x01\x02')
        root=contract("ReadRegister(5,16,'big','register')")
        with self.assertRaises(Unknown):transaction(root,root['behavior']['acquisition'],'spi')

    def test_register_value_is_encoded_once_and_header_cannot_hide_payload(self):
        encoding="{'address_bits':8,'address_byte_order':'big','read_header':['address'],'write_header':['address'],'dummy_read_bytes':0,'auto_increment':False}"
        for value,expected,environment in [("'1 | (3 << 2)'",13,None),("'saved_value'",9,{'saved_value':9}),('-2',254,None)]:
            root=contract("WriteRegister(5,"+value+",8,'big',encoding="+encoding+")")
            wire=transaction(root,root['behavior']['acquisition'],'i2c',environment=environment)
            self.assertEqual(wire.write,(5,expected))
        root=contract("WriteRegister(5,9,8,'big',encoding="+encoding.replace("'write_header':['address']","'write_header':['address','value']")+")")
        with self.assertRaisesRegex(ContractError,'write value bytes are appended'):transaction(root,root['behavior']['acquisition'],'i2c')
        for value,error in [("'1/2'",ContractError),("'256'",ContractError),("'missing_value'",Unknown)]:
            root=contract("WriteRegister(5,"+value+",8,'big',encoding="+encoding+")")
            with self.assertRaises(error):transaction(root,root['behavior']['acquisition'],'i2c')

    def test_i2c_write_read_and_spi_write_discard(self):
        root=contract("Transfer([WriteSegment([1]),ReadSegment(2,'data')],'stop')")
        plan=transaction(root,root['behavior']['acquisition'],'i2c');plan.check({'write':[1],'read_len':2})
        self.assertEqual(plan.project([2,3]),{'data':b'\x02\x03'})
        root=contract('WriteBytes([3,4])');plan=transaction(root,root['behavior']['acquisition'],'spi')
        for n in (0,2):plan.check({'write':[3,4],'read_len':n})
        self.assertEqual(plan.project([9,8]),b'')

    def test_value_too_large_for_declared_bits_is_violated_before_adapter_gap(self):
        from method.autodriver.implementation.system.contract.check import check_contract
        root="DeviceContract('synthetic',SPI(0,'selected'),FrozenPolicy(),{},Lifecycle(Sequence(),WriteRegister(1,128,1,'big',encoding={'address_bits':3,'address_byte_order':'big','read_header':['address'],'write_header':['address'],'dummy_read_bytes':0,'auto_increment':False})))"
        report=check_contract(root,'dbc')[1]
        self.assertEqual(report['status'],'violated')
        self.assertTrue(any(f['check']=='typed_wire_transaction' and f['status']=='violated' for f in report['findings']))


if __name__=='__main__':unittest.main()
