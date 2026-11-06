"""Public per-node activation probes with synthetic contracts only."""
import json
import unittest

from tests.regression.test_autodriver_stimuli import model
from method.autodriver.implementation.system.contract.model import node
from method.autodriver.implementation.system.native.nuttx import evaluate


class NativeActivationTests(unittest.TestCase):
    def report(self,probe,result):
        dbc=model('Lifecycle(Sequence(),Sequence(),recovery=None,reentry=None)')
        plan={'cases':[{'case':'probe','probe':probe,'registration_failure':0,'bus_failure':0,'expected_valid':True}],
              'scope':'synthetic single-property trace, not candidate execution'}
        events=[{'phase':'case_begin','case':'probe'},
                {'phase':'request_boundary','tick':0,'frequency':1000000,'instance':'one'},
                {'phase':'native_return','tick':0,'frequency':1000000,'request':'fetch','result':result,'bytes':16,'elapsed_us':0,'probe_mode':probe},
                {'phase':'case_end'}]
        return evaluate(dbc,node('FrameworkProfile'),node('BindingPlan'),plan,'\n'.join(map(json.dumps,events)))

    def test_disable_is_scoped_to_the_selected_native_node(self):
        bad=self.report(4,-19);good=self.report(4,16)
        self.assertIn('another native node',bad['cases'][0]['violations'][0]);self.assertEqual(good['cases'][0]['violations'],[])
        self.assertEqual(good['status'],'unknown')

    def test_disabled_and_invalid_buffer_cannot_return_success(self):
        for probe in (1,2,3):
            with self.subTest(probe=probe):
                self.assertTrue(self.report(probe,16)['cases'][0]['violations'])
                self.assertEqual(self.report(probe,-22)['cases'][0]['violations'],[])
