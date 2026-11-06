"""Exercise the standalone entry points and fail-closed content boundaries."""
from pathlib import Path
import json
import tempfile
import unittest

from benchmark.common.api import ROOT, identity, load, save
from benchmark.common.freeze_scope import owned_files, scopes_record, verify_scope
from tests.common.experiment import expand
from tests.common.execution.config import load_host


class StandaloneDistributionTests(unittest.TestCase):
    def test_documented_plans_use_only_the_current_sd_method(self):
        for name, count in [('smoke', 1), ('all44', 44)]:
            plan = expand(load(ROOT / f'examples/{name}.yaml'), live=True)
            self.assertEqual(len(plan['trials']), count)
            self.assertTrue(all(row['method'] == 'autodriver' and row['condition'] == 'SD'
                                for row in plan['trials']))
            self.assertEqual(plan['model_calls'], 0)

    def test_unavailable_method_and_live_condition_are_rejected_before_execution(self):
        spec = load(ROOT / 'examples/smoke.yaml')
        spec['methods'] = ['unbundled_method']
        with self.assertRaises(ValueError):
            expand(spec, live=True)
        spec['methods'] = ['autodriver']
        spec['conditions'] = ['S']
        with self.assertRaises(ValueError):
            expand(spec, live=True)

    def test_distributed_credentials_cannot_start_a_paid_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / '.env'
            path.write_bytes((ROOT / '.env.example').read_bytes())
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'Configure your own'):
                load_host(root)

    def test_method_content_drift_is_refused_but_new_generated_cache_is_not_an_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            method = root / 'method/autodriver/adapter.py'
            method.parent.mkdir(parents=True)
            method.write_text('def run(request): return request\n')
            files = owned_files(root)
            manifest = dict(schema='autodriver.standalone-artifact/v1', status='extracted',
                            files=files, dependency_scopes=scopes_record(files, {}))
            manifest['release_identity'] = identity(manifest)
            save(root / 'manifest.json', manifest)
            self.assertEqual(verify_scope('autodriver', root)['status'], 'pass')
            save(root / 'method/autodriver/knowledge/new/fp.json', {'generated': True})
            self.assertEqual(verify_scope('autodriver', root)['status'], 'pass')
            method.write_text('def run(request): return None\n')
            with self.assertRaisesRegex(ValueError, 'materials changed'):
                verify_scope('autodriver', root)


if __name__ == '__main__':
    unittest.main()
