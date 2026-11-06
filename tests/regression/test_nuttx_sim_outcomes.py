"""Missing SIM capabilities and transport timeouts retain distinct evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from benchmark.common.evaluation.nuttx.sim.adapter import run_cases


class SimOutcomes(unittest.TestCase):
    def evaluate(self, scenarios):
        report = {"cases": [], "diagnostics": [], "model_checks": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def execute(linked, arguments, log):
                scope, code, timeout, text = scenarios[arguments[0]]
                log.write_text(text)
                return {"status": "pass" if code == 0 else "fail", "returncode": code,
                        "timed_out": timeout}

            cases = [{"id": name, "scope": row[0]} for name, row in scenarios.items()]
            with patch("benchmark.common.evaluation.nuttx.sim.adapter.execute", execute):
                run_cases(None, root, cases, report, root)
        return report

    def test_missing_core_capability_blocks_instead_of_failing_candidate(self):
        report = self.evaluate({"core": ("core", 87, False,
            "BENCHSIM model_unsupported capability=register_x")})
        self.assertEqual(report["status"], "model_unsupported")
        self.assertEqual(report["cases"][0]["reason_code"], "model_capability_missing")
        self.assertEqual(report["cases"][0]["missing_capability"], "register_x")

    def test_failed_selfcheck_overrides_any_candidate_conclusion(self):
        report = self.evaluate({"model": ("internal", 86, False, "BENCHSIM assertion"),
                                "core": ("core", 0, False, "")})
        self.assertEqual(report["status"], "infrastructure_error")

    def test_diagnostic_capability_gap_is_reported_without_changing_core(self):
        report = self.evaluate({"core": ("core", 0, False, ""),
            "diagnostic": ("diagnostic", 87, False, "BENCHSIM model_unsupported capability=x")})
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["diagnostics"][0]["status"], "model_unsupported")

    def test_host_timeout_is_not_inferred_as_candidate_assertion(self):
        report = self.evaluate({"core": ("core", -9, True, "")})
        self.assertEqual(report["status"], "not_completed")
        self.assertEqual(report["cases"][0]["reason_code"], "host_deadline")

    def test_exit_code_alone_cannot_claim_missing_capability(self):
        report = self.evaluate({"core": ("core", 87, False, "")})
        self.assertEqual(report["cases"][0]["reason_code"], "process_failed")

    def test_truncated_capability_marker_is_not_a_known_model_gap(self):
        report = self.evaluate({"core": ("core", 87, False,
                                        "BENCHSIM model_unsupported capability=\n")})
        self.assertEqual(report["cases"][0]["reason_code"], "process_failed")
        self.assertNotIn("missing_capability", report["cases"][0])

    def test_host_timeout_retains_precedence_over_partial_marker(self):
        report = self.evaluate({"core": ("core", 87, True,
                                        "BENCHSIM model_unsupported capability=x")})
        self.assertEqual(report["status"], "not_completed")
        self.assertEqual(report["cases"][0]["reason_code"], "host_deadline")
        self.assertNotIn("missing_capability", report["cases"][0])


if __name__ == "__main__":
    unittest.main()
