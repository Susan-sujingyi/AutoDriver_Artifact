"""Distinguish returned assertions from process failures without guessing blame."""
import unittest

from benchmark.common.evaluation.report import case_result


class AssertionClassification(unittest.TestCase):
    def observation(self, **changes):
        case = {"id": "sample", "scope": "core", "status": "fail",
                "failure_class": "fail", "detail": "attempt to multiply with overflow",
                "execution": {"returncode": 0, "timed_out": False}}
        case.update(changes)
        return case_result(case, "/behavior/cases/0")

    def test_parsed_assertion_is_not_a_process_failure(self):
        result = self.observation()
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["reason_code"], "behavior_assertion_unclassified")
        self.assertEqual(result["responsibility"], "unknown")
        self.assertEqual(result["confidence"], "confirmed")

    def test_untyped_assertion_does_not_guess_candidate_responsibility(self):
        for detail in ("index out of bounds", "test assertion", "unexpected internal state"):
            with self.subTest(detail=detail):
                self.assertEqual(self.observation(detail=detail)["responsibility"], "unknown")

    def test_typed_contract_preserves_candidate_inference(self):
        result = self.observation(failure_class="PROTOCOL",
                                  detail="PROTOCOL: I2C operation without enabling the device")
        self.assertEqual(result["reason_code"], "contract_assertion_failed")
        self.assertEqual(result["responsibility"], "candidate")
        self.assertEqual(result["confidence"], "inferred")

    def test_missing_record_and_nonzero_process_remain_distinct(self):
        cases = ({"status": "execution_error"},
                 {"execution": {"returncode": -9, "timed_out": False}},
                 {"execution": {"timed_out": False}})
        for changes in cases:
            with self.subTest(changes=changes):
                result = self.observation(**changes)
                self.assertEqual(result["reason_code"], "process_or_record_failure")
                self.assertEqual(result["responsibility"], "unknown")

    def test_timeout_is_not_a_completed_assertion(self):
        result = self.observation(execution={"returncode": -9, "timed_out": True})
        self.assertEqual(result["reason_code"], "host_deadline")

    def test_missing_capability_remains_scoring_blocked(self):
        result = self.observation(status="model_unsupported")
        self.assertEqual(result["reason_code"], "model_capability_missing")
        self.assertEqual(result["responsibility"], "scoring_facility")

    def test_model_assertion_never_becomes_candidate_failure(self):
        result = self.observation(scope="internal", failure_class="PROTOCOL")
        self.assertEqual(result["reason_code"], "model_self_check_failed")
        self.assertEqual(result["responsibility"], "scoring_facility")


if __name__ == "__main__":
    unittest.main()
