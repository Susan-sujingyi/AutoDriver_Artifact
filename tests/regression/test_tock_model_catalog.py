"""A model check present in Rust must not silently disappear from scoring."""
import re
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmark.common.api import ROOT, load, owned, resolve_task, task_paths
from benchmark.common.evaluation.tock import adapter
from benchmark.common.evaluation.tock.adapter import validate_catalog


class TockModelCatalog(unittest.TestCase):
    def test_evaluator_blocks_a_catalog_with_no_model_checks_before_build(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'run') as name:
            root = Path(name)
            directory = root / 'model'
            directory.mkdir()
            (directory / 'profile.json').write_text('{}')
            (directory / 'catalog.json').write_text(json.dumps({
                'cases': [{'id': 'numeric/example', 'scope': 'core'}]}))
            with patch.object(adapter, 'resources', return_value=directory):
                report = adapter.evaluate_driver(SimpleNamespace(key='fixture'),
                                                 root / 'absent-candidate.rs', ROOT, root / 'work')
            self.assertEqual(report['status'], 'infrastructure_error')
            self.assertIn('Missing required case scope: internal', report['reason'])
            self.assertFalse(report['compiled'])
            self.assertFalse(report['software_behavior_pass'])

    def test_missing_model_checks_cannot_pass_vacuously(self):
        core = {'id': 'numeric/example', 'scope': 'core'}
        check = {'id': '__model/example', 'scope': 'internal'}
        validate_catalog([core, check])
        for catalog in ([], [core], [check], [core, core, check],
                        [core, {'id': 'model', 'scope': 'unknown'}], [core, None]):
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                validate_catalog(catalog)

    def test_every_profile_executes_its_declared_self_checks(self):
        seen = set()
        tasks = 0
        for name in task_paths():
            task = resolve_task(name)
            if task.os != 'tock': continue
            tasks += 1
            directory = owned(task.suite['adapters']['tock/' + task.bus]['directory'])
            if directory in seen: continue
            seen.add(directory)
            with self.subTest(profile=str(directory)):
                cases = load(directory / 'catalog.json')['cases']
                validate_catalog(cases)
                ids = [case['id'] for case in cases]
                self.assertEqual(len(ids), len(set(ids)))
                checks = {case['id'] for case in cases if case['scope'] == 'internal'}
                self.assertTrue(checks, 'No independently executed model checks')
                self.assertTrue(any(case['scope'] == 'core' for case in cases))
                declared = set(re.findall(r'run\(\s*"(__model/[^\"]+)"\s*,\s*"internal"',
                                          (directory / 'harness.rs').read_text()))
                self.assertTrue(declared <= checks,
                                'Rust self-checks missing from execution catalog: ' + str(declared - checks))
        self.assertEqual(tasks, 27)
        self.assertEqual(len(seen), 17)


if __name__ == '__main__':
    unittest.main()
