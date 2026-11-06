"""Root-cause precedence and independent delivery gates, entirely offline."""
from copy import deepcopy
import unittest
import jsonschema
from benchmark.common.api import ROOT, load

from benchmark.common.evaluation.report import explain, summary_columns, new_report, attach_generation


class EvaluationV2(unittest.TestCase):
    def report(self, **behavior):
        base = {"status": "pass", "compiled": True,
                "model_checks": [{"id": "model", "scope": "internal", "status": "pass"}],
                "cases": [{"id": "sample", "scope": "core", "status": "pass"}], "diagnostics": []}
        base.update(behavior)
        return {"task_id": "test", "model_calls": 0, "hardware_status": "not_run", "validation_identity": "test",
                "status": "pass", "software_status": "pass", "candidate_identity": "frozen-candidate",
                "native_build": {"status": "pass"}, "behavior": base}

    def explain(self, report, **kwargs):
        explain(report, entered=["submission", "native_build", "model_build"], **kwargs)
        jsonschema.validate(report, load(ROOT / "benchmark/common/schemas/evaluation.schema.json"))
        return report

    def test_success_requires_model_and_core_evidence(self):
        result = self.explain(self.report())
        self.assertIsNone(result["failure"])
        self.assertEqual(result["schema"], "autodriver.evaluation/v2")
        self.assertEqual([s["outcome"] for s in result["stages"]],
                         ["not_run", "pass", "pass", "pass", "pass", "not_run", "not_run"])

    def test_missing_core_never_claims_pass(self):
        for behavior in ({"cases": []}, {"model_checks": []}, {"expected_core_count": 2}):
            result = self.explain(self.report(**behavior))
            self.assertEqual(result["status"], "model_unsupported")
            self.assertEqual(result["software_status"], "model_unsupported")
            self.assertEqual(result["failure"]["reason_code"], "required_coverage_missing")

    def test_native_success_model_compile_failure_is_scoring_blocker(self):
        report = self.report(status="compile_failed", compiled=False, cases=[], model_checks=[])
        report.update(status="model_unsupported", software_status="model_unsupported")
        result = self.explain(report)
        self.assertEqual(result["failure"]["stage"], "model_build")
        self.assertEqual(result["failure"]["reason_code"], "native_model_compile_mismatch")
        self.assertEqual(result["result_class"], "scoring_blocked")

    def test_model_failure_overrides_candidate_assertion(self):
        report = self.report(model_checks=[{"id": "bus", "scope": "internal", "status": "fail"}],
                             cases=[{"id": "sample", "scope": "core", "status": "fail",
                                     "reason_code": "assertion_failed"}])
        report.update(status="infrastructure_error", software_status="infrastructure_error")
        before = deepcopy(report["behavior"])
        result = self.explain(report)
        self.assertEqual(result["failure"]["reason_code"], "model_self_check_failed")
        self.assertEqual(result["result_class"], "scoring_blocked")
        self.assertEqual(result["behavior"], before)

    def test_diagnostic_failure_does_not_replace_core_pass(self):
        result = self.explain(self.report(diagnostics=[{"id": "delay", "scope": "diagnostic", "status": "fail"}]))
        self.assertEqual(result["software_status"], "pass")
        self.assertIsNone(result["failure"])
        self.assertEqual(result["case_results"][-1]["outcome"], "blocked")

    def test_observation_limits_are_not_method_limits(self):
        for reason in ("event_budget", "request_rejected", "request_admission_deadline"):
            report = self.report(cases=[{"id": "sample", "scope": "core", "status": "not_completed",
                                        "detail": f"NOT_COMPLETED: reason={reason} time_us=24000"}])
            report.update(status="not_completed", software_status="not_completed")
            result = self.explain(report)
            self.assertEqual(result["failure"]["reason_code"], reason)
            self.assertEqual(result["failure"]["responsibility"], "unknown")
            self.assertEqual(result["result_class"], "unresolved")

    def test_host_deadline_and_sigkill_do_not_prove_oom(self):
        for timed_out, reason in ((True, "host_deadline"), (False, "process_or_record_failure")):
            report = self.report(cases=[{"id": "sample", "scope": "core", "status": "execution_error",
                                        "execution": {"returncode": -9, "timed_out": timed_out}}])
            report.update(status="not_completed", software_status="not_completed")
            result = self.explain(report)
            self.assertEqual(result["failure"]["reason_code"], reason)
            self.assertEqual(result["failure"]["responsibility"], "unknown")

    def test_contract_mismatch_is_inference_not_proven_root_cause(self):
        report = self.report(cases=[{"id": "sample", "scope": "core", "status": "fail",
                                    "failure_class": "CALLBACK", "detail": "CALLBACK: wrong completion"}])
        report.update(status="behavior_failed", software_status="behavior_failed")
        result = self.explain(report)
        self.assertEqual(result["failure"]["responsibility"], "candidate")
        self.assertEqual(result["failure"]["confidence"], "inferred")
        self.assertTrue(result["failure"]["evidence"])

    def test_scoring_capability_has_priority_over_other_assertion(self):
        report = self.report(cases=[{"id": "assertion", "scope": "core", "status": "fail",
                                     "reason_code": "assertion_failed"},
                                    {"id": "unsupported", "scope": "core", "status": "model_unsupported"}])
        report.update(status="model_unsupported", software_status="model_unsupported")
        self.assertEqual(self.explain(report)["result_class"], "scoring_blocked")

    def test_packaging_failure_preserves_software_conclusion(self):
        report = self.report()
        report.update(status="infrastructure_error", firmware={"status": "infrastructure_error", "reason": "disk full"})
        result = self.explain(report)
        self.assertEqual(result["software_status"], "pass")
        self.assertEqual(result["failure"]["stage"], "package")
        self.assertEqual(result["failure"]["recovery"]["action"], "package_existing_native_build")

    def test_integrity_failure_does_not_claim_candidate_authored_tamper(self):
        report = self.report()
        report.update(status="submission_rejected", software_status="submission_rejected")
        result = self.explain(report, integrity_issue="candidate_changed")
        self.assertEqual(result["failure"]["reason_code"], "candidate_changed")
        self.assertEqual(result["failure"]["responsibility"], "unknown")

    def test_legacy_reports_not_relabelled_from_status_alone(self):
        for status in ("not_completed", "submission_rejected", "compile_failed"):
            columns = summary_columns({"status": status})
            self.assertEqual(columns["result_class"], "unclassified_legacy")
            self.assertEqual(columns["reason_code"], "")

    def test_unexplained_backend_failure_remains_unresolved(self):
        report = self.report()
        report.update(status="infrastructure_error", software_status="infrastructure_error")
        result = self.explain(report)
        self.assertEqual(result["result_class"], "unresolved")
        self.assertEqual(result["failure"]["reason_code"], "unclassified_failure")

    def test_api_loss_keeps_transport_phase_and_precedes_empty_submission(self):
        report = new_report("test", "frozen")
        attach_generation(report, {"termination": {"retryable": True, "reason": "provider_or_network",
            "operation": "upstream_body", "error_type": "IncompleteRead", "response_bytes_received": 350}},
            submission_state="no_target_changes")
        jsonschema.validate(report, load(ROOT / "benchmark/common/schemas/evaluation.schema.json"))
        self.assertEqual(report["failure"]["responsibility"], "provider_or_transport")
        self.assertEqual(report["failure"]["operation"], "upstream_body")
        self.assertEqual(report["failure"]["causal_chain"][-1]["reason_code"], "no_target_changes")
        self.assertEqual(report["software_status"], "not_run")

    def test_method_limit_does_not_discard_final_patch_verdict(self):
        report = self.explain(self.report())
        attach_generation(report, {"timed_out": True, "termination": {"kind": "method_limit", "reason": "wall_time"}})
        self.assertEqual(report["software_status"], "pass")
        self.assertEqual(report["result_class"], "budget_terminated")
        self.assertEqual(report["failure"]["reason_code"], "method_wall_time")

    def test_provider_error_does_not_hide_invalid_submission(self):
        report = new_report("test", "frozen")
        report.update(status="submission_rejected", reason="forbidden path")
        explain(report, entered=["submission"])
        attach_generation(report, {"termination": {"retryable": True, "reason": "provider_or_network"}})
        self.assertEqual(report["failure"]["stage"], "submission")
        self.assertEqual(report["result_class"], "candidate_failure")


if __name__ == "__main__":
    unittest.main()
