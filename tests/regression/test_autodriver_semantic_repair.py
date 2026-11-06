"""Deterministic contract errors use bounded single-object repair, offline."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.autodriver_fixtures import make_fixture
from method.autodriver.implementation.system.calls import CallRunner,RejectedResponse
from method.autodriver.implementation.system.evidence import Evidence


class SemanticRepairTests(unittest.TestCase):
    def test_bad_physical_expression_and_rounding_repair_only_their_own_object(self):
        for kind,stage in [('dbc','dbc_recover'),('bp','bp_build')]:
            for persistent in (False,True):
                with self.subTest(kind=kind,persistent=persistent),tempfile.TemporaryDirectory() as folder:
                    visible,registry,requests,responses=make_fixture(folder);seen=[]
                    def client(prompt,**kwargs):
                        data=json.loads(prompt);seen.append(data);call=data['call']
                        response=deepcopy(responses[stage]);response.update(call_id=call['call_id'],stage=call['stage'])
                        bad=persistent or len(seen)==1
                        if kind=='dbc':
                            expression='1 / (' if bad else '1 / 2'
                            response['artifact']['content']="DeviceContract('synthetic',I2C(17),FrozenPolicy(),{},Lifecycle(Sequence(),Sequence()),quantities={'value':PhysicalQuantity("+repr(expression)+",'1')})"
                        else:
                            rounding='invalid' if bad else 'floor'
                            response['artifact']['content']="BindingPlan(Ref('dbc'),Ref('target_fp'),Ref('support'),'synthetic',obligations=[Rounding("+repr(rounding)+")])"
                        if len(seen)>1:
                            self.assertEqual(call['stage'],'repair');self.assertEqual(call['repair_target'],kind)
                            diagnostic=json.loads(next(c['text'] for c in data['context'] if c['role']=='internal_diagnostic'))
                            self.assertEqual(diagnostic['category'],'contract_semantics')
                            self.assertEqual(diagnostic['semantic_checks']['status'],'violated')
                            self.assertTrue(any(c['role']=='candidate' for c in data['context']))
                        return json.dumps(response)
                    runner=CallRunner(Evidence(visible,registry),Path(folder)/'calls',client=client,maximum_repairs=1)
                    if persistent:
                        with self.assertRaises(RejectedResponse):runner.recover(requests[stage])
                    else:
                        response,checks,_=runner.recover(requests[stage]);self.assertEqual(response['artifact']['kind'],kind)
                        self.assertNotEqual(checks['semantic_validation']['status'],'violated')
                    self.assertEqual(len(seen),2)
                    self.assertEqual(runner.calls[0]['semantic_status'],'violated')
