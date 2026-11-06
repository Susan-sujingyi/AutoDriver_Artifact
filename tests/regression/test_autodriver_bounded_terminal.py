"""Algorithm bounds/rejected output are completed no-submission outcomes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.regression.test_autodriver_pipeline import prepared
from method.autodriver.implementation.system.pipeline import Pipeline
from method.autodriver.implementation.system.protocol.assembler import ContextLimitError,ProtocolError
from tests.common.execution.termination import classify
from tests.common.autodriver_submission import no_submission_report


class BoundedTerminalTests(unittest.TestCase):
    def test_bad_model_envelopes_exhaust_one_bounded_job_without_infrastructure_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);prepared(root);calls=[]
            def client(*args,**kwargs):calls.append(kwargs);return '{bad JSON'
            pipeline=Pipeline(root,client=client);result=pipeline.run()
            self.assertEqual(len(calls),3);self.assertEqual(result['status'],'upstream_response_rejected');self.assertIsNone(result['candidate'])
            execution={'status':'pass','result':{'method':'autodriver','status':result['status']}}
            execution['termination']=classify(execution,[])
            self.assertEqual(execution['termination']['kind'],'completed');self.assertFalse(execution['termination']['retryable'])
            report=no_submission_report('synthetic-sd','fixture',execution)
            self.assertTrue(report['trial_execution_complete']);self.assertEqual(report['generation_outcome'],'no_submission')

    def test_context_bound_and_call_bound_have_explicit_limit_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);prepared(root);pipeline=Pipeline(root,client=lambda *a,**k:self.fail('no transport before assembly'))
            with patch('method.autodriver.implementation.system.calls.assemble',side_effect=ContextLimitError(900,800)):
                result=pipeline.run()
            self.assertEqual(result['status'],'context_limit_exhausted');self.assertEqual(result['failure']['required_bytes'],900)
            execution={'status':'pass','result':{'method':'autodriver','status':result['status']}}
            self.assertEqual(classify(execution,[])['kind'],'method_limit')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);prepared(root);pipeline=Pipeline(root,client=lambda *a,**k:'{bad',maximum_calls=1)
            result=pipeline.run();self.assertEqual(result['status'],'method_limit_exhausted');self.assertEqual(result['failure']['bound'],'logical_calls')
            self.assertEqual(result['failure']['used'],1)

    def test_saved_identity_errors_and_unexpected_failures_are_not_masked_as_model_failure(self):
        for error in (ProtocolError('saved response identity changed; refuse network fallback'),OSError('synthetic filesystem failure')):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);prepared(root);pipeline=Pipeline(root,client=lambda *a,**k:None)
                with patch.object(pipeline,'select',side_effect=error),self.assertRaises(type(error)):pipeline.run()
