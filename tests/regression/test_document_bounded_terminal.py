"""Unusable bounded code output is a completed failure, never an infra retry."""
import json,tempfile,unittest
from pathlib import Path

from test_framework_action_semantics import public_workspace
from method.autodriver.implementation.system.document_pipeline import DocumentPipeline
from tests.common.execution.termination import classify
from tests.common.autodriver_submission import no_submission_report


class DocumentBoundedTerminalTests(unittest.TestCase):
    def test_invalid_code_exhausts_four_repairs_once_without_creating_or_retrying_a_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);public_workspace(root);calls=[]
            def client(prompt,**kwargs):
                payload=json.loads(prompt);calls.append(payload['call'])
                return '{broken code response' if payload['expected_artifact_kind']=='code' else 'Readable primary; provider guarantees remain unproved.'
            pipeline=DocumentPipeline(root,client=client);result=pipeline.run()
            self.assertEqual(result['status'],'code_response_repair_exhausted')
            self.assertEqual(pipeline.code_repairs_used(),4);self.assertEqual(len(calls),10)
            self.assertIsNone(pipeline.code_proposal)
            execution=dict(status='pass',result=dict(method='autodriver',status=result['status']))
            execution['termination']=classify(execution,[])
            self.assertEqual(execution['termination'],dict(kind='completed',reason='code_response_repair_exhausted',retryable=False))
            report=no_submission_report('synthetic','validation',execution)
            self.assertTrue(report['trial_execution_complete']);self.assertEqual(report['generation_outcome'],'no_submission')
            self.assertFalse((root/'target/drivers/sensors/synthetic.c').exists())


if __name__=='__main__':unittest.main()
