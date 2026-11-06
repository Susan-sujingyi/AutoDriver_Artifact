"""Reject non-factual citations through real bounded response recovery."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tests.regression.autodriver_fixtures import make_fixture
from method.autodriver.implementation.system.calls import CallRunner, RejectedEnvelope
from method.autodriver.implementation.system.evidence import Evidence
from method.autodriver.implementation.system.protocol.assembler import digest


class CitationRecoveryTests(unittest.TestCase):
    def run_fixture(self, root, *, always_invalid=False):
        visible, registry, requests, responses = make_fixture(root)
        path = visible / 'input/nonfactual-index.json'
        path.write_text('{"facts_available":false,"original":"source"}\n')
        registry['materials'].append({
            'id': 'nonfactual-index', 'path': path.relative_to(visible).as_posix(),
            'kind': 'source_index', 'approved': True, 'os_role': 'source',
            'sha256': digest(path.read_bytes()), 'parents': ['source'],
        })
        requests['dbc_recover']['selections'].append({
            'role': 'source_facts', 'material_id': 'nonfactual-index'})
        seen = []

        def client(prompt, **kwargs):
            data = json.loads(prompt)
            seen.append(data)
            response = deepcopy(responses['dbc_recover'])
            response.update(call_id=data['call']['call_id'], stage=data['call']['stage'])
            index = next(c for c in data['context'] if c['material_id'] == 'nonfactual-index')
            self.assertFalse(index['evidence_eligible'])
            if len(seen) > 1:
                diagnostic = json.loads(next(c['text'] for c in data['context']
                    if c['role'] == 'internal_diagnostic'))
                self.assertTrue(diagnostic['opaque_invalid_envelope'])
                self.assertNotIn('nonfactual-index', {
                    c['material_id'] for c in diagnostic['source_citation_inventory']})
            if len(seen) == 1 or always_invalid:
                response.update(outcome='needs_evidence', artifact=None,
                    evidence_links=[{'claim': 'untrusted index claim', 'basis': 'invalid',
                                     'citations': [index['citation_id']]}],
                    context_requests=[{'scope': 'source', 'query': 'acquire',
                        'question': 'Read original source', 'reason': 'Synthetic recovery fixture'}])
            return json.dumps(response)

        runner = CallRunner(Evidence(visible, registry), Path(root) / 'calls', client=client)
        return runner, requests['dbc_recover'], seen

    def test_artifactless_bad_index_citation_can_be_repaired_without_admitting_it(self):
        with tempfile.TemporaryDirectory() as root:
            runner, request, seen = self.run_fixture(root)
            _, checks, _ = runner.recover(request)
            self.assertTrue(checks['transport_valid'])
            self.assertEqual(len(seen), 2)
            first = runner.output / (request['call_id'] + '.checks.json')
            rejection = json.loads(first.read_text())
            self.assertFalse(rejection['transport_valid'])
            self.assertIn('not eligible', rejection['reason'])
            self.assertEqual(runner.calls[0]['error_type'], 'RejectedEnvelope')

    def test_repeated_bad_index_citation_uses_original_three_response_bound(self):
        with tempfile.TemporaryDirectory() as root:
            runner, request, seen = self.run_fixture(root, always_invalid=True)
            with self.assertRaisesRegex(RejectedEnvelope, 'not eligible'):
                runner.recover(request)
            self.assertEqual(len(seen), 3)
            self.assertTrue(all(c['status'] == 'error' for c in runner.calls))
            self.assertFalse(runner.dbc_candidate_seen)


if __name__ == '__main__':
    unittest.main()
