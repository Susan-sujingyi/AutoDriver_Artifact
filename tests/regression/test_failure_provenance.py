"""Real export validation and transport evidence must not conflate failures."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from benchmark.common.api import ROOT, inventory
from benchmark.common.submission import EmptySubmission, OutOfScopeSubmission, export_patch
from tests.common.execution.termination import classify


class FailureProvenance(unittest.TestCase):
    def test_export_distinguishes_empty_from_forbidden_changes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'run') as temp:
            root = Path(temp)
            baseline = root / 'baseline'
            candidate = root / 'candidate'
            baseline.mkdir()
            candidate.mkdir()
            (candidate / 'immutable.c').write_text('original\n')
            record = {'files': inventory(candidate)}
            task = SimpleNamespace(editable=lambda name: name == 'driver.c')
            with patch('benchmark.common.submission.verify_baseline', return_value=record):
                with self.assertRaises(EmptySubmission):
                    export_patch(task, baseline, candidate, root / 'output')
                self.assertFalse((root / 'output').exists())
                (candidate / 'immutable.c').write_text('changed\n')
                with self.assertRaisesRegex(OutOfScopeSubmission, 'immutable.c'):
                    export_patch(task, baseline, candidate, root / 'output')
                self.assertFalse((root / 'output').exists())

    def test_timeout_retains_phase_and_partial_response_evidence(self):
        for operation, received in [('upstream_headers', 0), ('upstream_body', 101596)]:
            with self.subTest(operation=operation):
                result = classify({}, [{'event': 'error', 'phase': operation,
                                        'error_type': 'TimeoutError',
                                        'response_bytes_received': received}])
                self.assertEqual(result['reason'], 'provider_or_network')
                self.assertTrue(result['retryable'])
                self.assertEqual(result['operation'], operation)
                self.assertEqual(result['response_bytes_received'], received)
                self.assertEqual(result['error_type'], 'TimeoutError')

    def test_local_settlement_error_is_not_an_upstream_timeout(self):
        result = classify({}, [{'event': 'error', 'phase': 'usage_settlement',
                                'error_type': 'TimeoutError'}])
        self.assertNotEqual(result.get('reason'), 'provider_or_network')
        self.assertEqual(result['reason'], 'response_settlement_failure')
        self.assertEqual(result['recovery_mode'], 'replay_response')
        self.assertFalse(result['new_generation_allowed'])

    def test_socket_delivery_failure_requires_client_state_review(self):
        result = classify({}, [{'event': 'error', 'phase': 'client_response', 'id': 'request',
                                'error_type': 'BrokenPipeError'}])
        self.assertEqual(result['reason'], 'response_delivery_uncertain')
        self.assertEqual(result['recovery_mode'], 'inspect_client_checkpoint')
        self.assertFalse(result['new_generation_allowed'])


if __name__ == '__main__':
    unittest.main()
