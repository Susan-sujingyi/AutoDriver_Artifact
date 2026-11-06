"""Framework structure and bounded semantic repair with fake responses only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.autodriver_fixtures import make_fixture
from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.contract.check import check_contract
from method.autodriver.implementation.system.calls import CallRunner,RejectedResponse


class FrameworkTypesTests(unittest.TestCase):
    def check(self,fields):
        return check_contract("FrameworkProfile('synthetic','snapshot',"+fields+")",'fp')[1]

    def test_i2c_summary_cannot_be_used_as_spi_or_gpio(self):
        summary="AsyncI2CContract('i','w','r',[],[],[],[],'p')"
        self.assertNotEqual(self.check('i2c='+summary)['status'],'violated')
        for key in ('spi','gpio'):
            for value in (summary,"{'api':"+summary+"}"):
                report=self.check(key+'='+value)
                self.assertEqual(report['status'],'violated')

    def test_unknown_is_distinct_from_wrong_typed_or_absent(self):
        for fields in ('spi=None',"spi=Unspecified('missing provider')","capabilities={'x':APIContract('x',Unspecified('missing parameter declaration'))}"):
            self.assertEqual(self.check(fields)['status'],'unknown')
        for fields in ("outputs=[]","capabilities={'x':APIContract('x',[])}",
                       "capabilities={'x':APIContract('x',{},completion=Callback('done','untyped arguments'))}",
                       "outputs={'value':SensorInterface('read',encoding=OutputEncoding('i32','degC',scale=True))}"):
            self.assertEqual(self.check(fields)['status'],'violated')

    def test_unknown_unit_is_not_a_type_error_and_quoted_offset_stays_invalid(self):
        for unit,expected in [("Unspecified('channel dependent')",'unknown'),("Unresolved('not recovered')",'unknown'),("[]",'violated')]:
            report=self.check("outputs={'value':SensorInterface('read',encoding=OutputEncoding('i32',"+unit+"))}")
            finding=next(row for row in report['findings'] if row['check']=='encoding_scale_and_unit')
            self.assertEqual(finding['status'],expected)
            self.assertNotIn('unhashable',finding['detail'])
        report=self.check("outputs={'value':SensorInterface('read',encoding=OutputEncoding('i32','degC',offset='0'))}")
        self.assertTrue(any(row['check']=='encoding_numeric_type' and row['status']=='violated' for row in report['findings']))

    def test_semantic_repair_is_fp_only_and_shares_the_repair_bound(self):
        for mode in ('fix','exhaust','unknown'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                visible,registry,requests,responses=make_fixture(temp);seen=[]
                def client(prompt,**kwargs):
                    data=json.loads(prompt);call=data['call'];seen.append(call)
                    response=deepcopy(responses['fp_build']);response.update(call_id=call['call_id'],stage=call['stage'])
                    wrong=mode=='exhaust' or mode=='fix' and len(seen)==1
                    field="AsyncI2CContract('i','w','r',[],[],[],[],'p')" if wrong else "Unspecified('not recovered')" if mode=='unknown' else 'None'
                    response['artifact']['content']="FrameworkProfile('ExampleTarget','synthetic-only',spi="+field+")"
                    if call['stage']=='repair':
                        self.assertEqual(call['repair_target'],'fp')
                        diagnostic=json.loads(next(c['text'] for c in data['context'] if c['role']=='internal_diagnostic'))
                        self.assertEqual(diagnostic['category'],'contract_semantics')
                        self.assertEqual(diagnostic['semantic_checks']['status'],'violated')
                    return json.dumps(response)
                runner=CallRunner(Evidence(visible,registry),Path(temp)/'calls',client=client,maximum_repairs=1)
                if mode=='exhaust':
                    with self.assertRaises(RejectedResponse):runner.recover(requests['fp_build'])
                else:self.assertNotEqual(runner.recover(requests['fp_build'])[1]['semantic_validation']['status'],'violated')
                self.assertEqual(len(seen),1 if mode=='unknown' else 2)
