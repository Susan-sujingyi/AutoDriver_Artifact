import copy
import unittest

from method.autodriver.implementation.system.semantic_documents import (
    ParentIdentity, SemanticDocument, advisory_generation_record,
)


class AdvisorySemanticDocuments(unittest.TestCase):
    def document(self, raw='readable contract draft', parents=()):
        return SemanticDocument('dbc', 'synthetic-task', 'call-1', 'dbc_recover', raw, parents)

    def test_unknown_liveness_and_violated_layout_remain_visible_but_allow_generation(self):
        observations = [dict(status='unknown', check='liveness', detail='finite scope'),
                        dict(status='violated', check='layout', detail='outside frame')]
        original = copy.deepcopy(observations)
        result = advisory_generation_record([self.document()], observations)
        self.assertTrue(result['generation_allowed'])
        self.assertEqual(result['observations'], original)
        self.assertEqual(result['contract_assurance'], 'not_asserted')
        result['observations'][0]['status'] = 'satisfied'
        self.assertEqual(observations, original)

    def test_malformed_or_non_constructor_text_remains_exact_original(self):
        for raw in ('a table and pseudocode', '{"artifact": broken', 'DeviceContract(unbalanced('):
            document = self.document(raw)
            result = advisory_generation_record([document], [])
            self.assertTrue(result['generation_allowed'])
            self.assertEqual(document.record()['raw_text'], raw)
            self.assertEqual(document.record()['executable_projection'], 'not_attempted')

    def test_claimed_pass_inside_raw_response_is_not_a_check(self):
        document = self.document('{"status":"satisfied","evidence_entailment":"proved"}')
        result = advisory_generation_record([document], [])
        self.assertEqual(result['observations'], [])
        self.assertEqual(document.record()['evidence_entailment'], 'unverified')
        self.assertEqual(result['contract_assurance'], 'not_asserted')

    def test_same_text_different_parent_revision_has_different_identity(self):
        first = self.document(parents=(ParentIdentity('source_fp', 'fp', '1'*64),))
        second = self.document(parents=(ParentIdentity('source_fp', 'fp', '2'*64),))
        self.assertEqual(first.raw_sha256, second.raw_sha256)
        self.assertNotEqual(first.identity, second.identity)

    def test_empty_response_does_not_fabricate_a_contract(self):
        result = advisory_generation_record([self.document('')], [])
        self.assertEqual(result['documents'][0]['availability'], 'empty_response')
        self.assertIn('bp', result['missing_contracts'])
        self.assertIn('dbc', result['missing_contracts'])
        self.assertTrue(result['generation_allowed'])

    def test_evidence_only_response_remains_raw_but_is_not_a_contract(self):
        document = SemanticDocument('dbc','synthetic','c1','dbc_recover',
                                    '{"tool_requests":[]}',(),response_role='evidence_only')
        result = advisory_generation_record([document],[])
        self.assertEqual(document.record()['raw_text'],'{"tool_requests":[]}')
        self.assertEqual(document.availability,'draft_not_produced')
        self.assertIn('dbc',result['missing_contracts'])

    def test_disabled_binding_is_explicit_and_not_an_empty_bp(self):
        result = advisory_generation_record([self.document()], [], expected_contracts=('dbc',))
        self.assertEqual(result['missing_contracts'], [])
        self.assertEqual([d['kind'] for d in result['documents']], ['dbc'])

    def test_source_target_profiles_cannot_share_an_unqualified_identity(self):
        source = SemanticDocument('fp', 'scope', 'call-1', 'fp_build', 'API facts', (), 'source')
        target = SemanticDocument('fp', 'scope', 'call-1', 'fp_build', 'API facts', (), 'target')
        self.assertEqual(source.raw_sha256, target.raw_sha256)
        self.assertNotEqual(source.identity, target.identity)
        with self.assertRaises(ValueError):
            SemanticDocument('fp', 'scope', 'call-1', 'fp_build', 'API facts', ())

    def test_host_identities_and_observation_labels_are_not_freeform_authority(self):
        with self.assertRaises(ValueError):
            ParentIdentity('private_reference', 'secret', '1'*64)
        with self.assertRaises(ValueError):
            ParentIdentity('dbc', 'contract', 'not-a-hash')
        with self.assertRaises(ValueError):
            advisory_generation_record([{'kind':'dbc', 'status':'pass'}], [])
        with self.assertRaises(ValueError):
            advisory_generation_record([self.document()], [{'status':'pass'}])

    def test_source_profile_cannot_fill_a_missing_target_profile(self):
        source = SemanticDocument('fp', 'scope', 'call-1', 'fp_build', 'API facts', (), 'source')
        result = advisory_generation_record([source], [], expected_contracts=('target_fp',))
        self.assertEqual(result['missing_contracts'], ['target_fp'])
        self.assertTrue(result['generation_allowed'])


if __name__ == '__main__':
    unittest.main()
