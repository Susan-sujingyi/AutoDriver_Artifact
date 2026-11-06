"""Evidence-based evaluation reporting, shared by both target OS backends.

This module explains observations; it does not define sensor correctness or
infer a process's cause of death from an exit status. Paths in evidence are
relative to the evaluation directory. Backend records remain unmodified.
"""
import re

STAGES = ("generation", "submission", "native_build", "model_build",
          "behavior_execution", "package", "archive")


def stage(name, outcome="not_run", reason="not_run", responsibility="unknown",
          confidence="unknown", evidence=(), *, action="none", prerequisites=()):
    return {"stage": name, "outcome": outcome, "reason_code": reason,
            "responsibility": responsibility, "confidence": confidence,
            "evidence": list(evidence), "causal_chain": [],
            "recovery": {"available": action != "none", "action": action,
                         "stage": name, "prerequisites": list(prerequisites)}}


def record(pointer):
    return {"path": "report.json", "pointer": pointer}


def new_report(task_id, validation_identity):
    return {"schema": "autodriver.evaluation/v2", "task_id": task_id,
            "validation_identity": validation_identity, "status": "infrastructure_error",
            "software_status": "not_run", "native_build_status": "not_run",
            "model_calls": 0, "hardware_status": "not_run",
            "stages": [stage(name) for name in STAGES], "case_results": [],
            "failure": None, "result_class": "unresolved"}


def attach_generation(report, execution, *, submission_state=None):
    """Controller-only evidence; preserve a scored final patch after a limit.

    A limit does not erase a partial candidate's software verdict. Conversely,
    API loss followed by an empty tree is not a candidate correctness verdict.
    Explicit invalid/forbidden submissions retain their independent rejection.
    """
    termination = execution.get("termination", {})
    reason = termination.get("reason")
    evidence = [{"path": "../execution/report.json", "pointer": "/termination"}]
    limits = {"wall_time": "method_wall_time", "Request-count limit reached": "method_request_budget",
              "Episode deadline reached": "method_wall_time",
              "Insufficient episode budget for the next request reservation": "method_cost_budget"}
    if execution.get("timed_out") or termination.get("kind") == "method_limit":
        code = "method_wall_time" if execution.get("timed_out") else limits.get(reason, "method_budget")
        item = stage("generation", "interrupted", code, "unknown", "confirmed", evidence)
    elif reason in ("provider_or_network", "provider_terminal_failure", "provider_protocol", "provider_quota") and termination.get("retryable"):
        item = stage("generation", "interrupted", reason, "provider_or_transport", "confirmed", evidence,
                     action="explicit_generation_recovery", prerequisites=("authorized_new_attempt", "provider_available"))
    elif reason in ("request_context_failure", "response_storage_failure", "response_settlement_failure", "response_delivery_claim_failed",
                    "response_delivery_uncertain", "response_replay_refused"):
        item = stage("generation", "interrupted", reason, "runtime_environment", "confirmed", evidence,
                     action=termination.get("recovery_mode", "inspect_receipt"),
                     prerequisites=("original_request_identity", "verified_client_delivery_state"))
        item["new_generation_allowed"] = False
    elif termination.get("kind") == "completed" or execution.get("result", {}).get("status") == "candidate_ready":
        item = stage("generation", "pass", "candidate_generated", confidence="confirmed", evidence=evidence)
    elif reason in ("interrupted", "campaign_budget") and termination.get("retryable"):
        item = stage("generation", "interrupted", reason, "runtime_environment", "confirmed", evidence,
                     action="explicit_generation_recovery", prerequisites=("authorized_new_attempt",))
    else:
        item = stage("generation", "blocked", "method_exit_unresolved", "unknown", "unknown", evidence,
                     action="inspect_execution_evidence")
    item["causal_chain"] = [{"stage": "generation", "reason_code": item["reason_code"], "evidence": evidence}]
    if submission_state:
        item["causal_chain"].append({"stage": "submission", "reason_code": submission_state})
    for key in ("operation", "error_type", "response_bytes_received", "http_status", "request_id"):
        if key in termination:
            item[key] = termination[key]
    stages = {s["stage"]: s for s in report.get("stages", [stage(n) for n in STAGES])}
    stages["generation"] = item
    report["stages"] = [stages[n] for n in STAGES]
    if (item["outcome"] != "pass" and report.get("status") != "submission_rejected" and
            (item["confidence"] == "confirmed" or not report.get("failure"))):
        report["failure"] = item
        report["result_class"] = result_class(item)
    return report


def case_result(case, pointer):
    """Classify structured observations without guessing sensor-specific bugs."""
    evidence = [record(pointer)]
    scope = case.get("scope", "core")
    state = case.get("status")
    run = case.get("execution", case)
    reason = case.get("reason_code")
    detail = case.get("detail", "")
    if state == "pass":
        result = stage("behavior_execution", "pass", "case_completed", evidence=evidence,
                       confidence="confirmed")
    elif scope == "internal":
        result = stage("behavior_execution", "blocked", "model_self_check_failed",
                       "scoring_facility", "confirmed", evidence,
                       action="repair_evaluator_then_rescore")
    elif run.get("timed_out") or detail == "case_process_time_limit" or reason == "host_deadline":
        result = stage("behavior_execution", "interrupted", "host_deadline", "unknown",
                       "confirmed", evidence, action="inspect_execution_evidence")
    elif state in ("model_unsupported", "not_exercised") or reason == "model_capability_missing":
        result = stage("behavior_execution", "blocked", "model_capability_missing",
                       "scoring_facility", "confirmed", evidence,
                       action="extend_evaluator_then_rescore")
    else:
        # Only documented executor tokens establish the kind of observation
        # limit. A bare not_completed is not evidence of a method budget limit.
        match = re.search(r"NOT_COMPLETED: reason=(event_budget|request_rejected|request_admission_deadline)\b", detail)
        if match:
            result = stage("behavior_execution", "blocked", match[1], "unknown",
                           "confirmed", evidence, action="inspect_execution_evidence")
        elif state == "not_completed":
            result = stage("behavior_execution", "blocked", "observation_incomplete", "unknown",
                           "unknown", evidence, action="inspect_execution_evidence")
        elif reason == "assertion_failed" or (state == "fail" and case.get("failure_class") in
                                              ("CONFIG", "PROTOCOL", "CALLBACK", "NUMERIC", "DATA")):
            # A model assertion is evidence of a mismatch. The model itself
            # may be wrong; a passing self-test does not prove all its rules.
            result = stage("behavior_execution", "failed", "contract_assertion_failed",
                           "candidate", "inferred", evidence,
                           action="inspect_contract_and_trace")
        elif state == "fail" and run.get("returncode") == 0:
            # A successfully parsed fail record is not a process/record failure.
            # Without a documented assertion category, attribution stays open.
            result = stage("behavior_execution", "failed", "behavior_assertion_unclassified",
                           "unknown", "confirmed", evidence, action="inspect_contract_and_trace")
        else:
            result = stage("behavior_execution", "blocked", "process_or_record_failure",
                           "unknown", "unknown", evidence, action="inspect_execution_evidence")
    return {"id": case.get("id"), "scope": scope, **result}


def explain(report, *, entered, exception=None, integrity_issue=None, package_requested=True):
    """Add v2 stages to a *new* evaluation; never rewrite historical records.

    entered is recorded at call boundaries, not reconstructed from the final
    status. Exceptions and integrity failures are explicit pipeline evidence.
    Generation/archive are owned by the controller, so remain not_run here.
    """
    stages = {name: stage(name) for name in STAGES}
    for name in entered:
        stages[name] = stage(name, "blocked", "stage_result_missing", evidence=[record("/status")],
                             action="inspect_execution_evidence")
    if "submission" in entered:
        if report.get("candidate_identity"):
            stages["submission"] = stage("submission", "pass", "submission_validated",
                                          confidence="confirmed", evidence=[record("/candidate_identity")])
        elif report.get("status") == "submission_rejected":
            stages["submission"] = stage("submission", "failed", "submission_invalid", "candidate",
                                          "confirmed", [record("/reason")])
    native = report.get("native_build")
    if native is not None:
        evidence = [record("/native_build")]
        if native.get("status") == "pass":
            item = stage("native_build", "pass", "native_build_passed", confidence="confirmed", evidence=evidence)
        elif native.get("configuration_error") or native.get("command_registration", {}).get("status") == "compile_failed":
            item = stage("native_build", "blocked", "target_integration_failed", "scoring_facility",
                         "confirmed", evidence, action="repair_evaluator_then_rescore")
        elif any(s.get("timed_out") for s in native.get("steps", [])):
            item = stage("native_build", "interrupted", "host_deadline", "unknown", "confirmed",
                         evidence, action="inspect_execution_evidence")
        else:
            item = stage("native_build", "failed", "native_build_failed", "unknown", "unknown",
                         evidence, action="inspect_build_diagnostics")
        stages["native_build"] = item
    behavior = report.get("behavior", {})
    build = behavior.get("build", {})
    steps = behavior.get("steps", [])
    failed_step = next((s for s in steps if s.get("status") != "pass"), None)
    model_compiled = (behavior.get("compiled") is True or bool(behavior.get("executable_sha256")))
    if model_compiled:
        stages["model_build"] = stage("model_build", "pass", "model_build_passed", confidence="confirmed",
                                      evidence=[record("/behavior")])
    elif behavior:
        evidence = [record("/behavior")]
        timeout = build.get("timed_out") or (failed_step or {}).get("timed_out")
        compile_mismatch = (behavior.get("status") in ("compile_failed", "candidate_compile_failed") or
                            (failed_step or {}).get("stage") == "candidate_compile")
        if timeout:
            item = stage("model_build", "interrupted", "host_deadline", "unknown", "confirmed",
                         evidence, action="inspect_execution_evidence")
        elif behavior.get("reason") == "public_interface_mismatch":
            item = stage("model_build", "failed", "public_interface_mismatch", "candidate",
                         "confirmed", evidence)
        elif compile_mismatch and (native or {}).get("status") == "pass":
            item = stage("model_build", "blocked", "native_model_compile_mismatch", "scoring_facility",
                         "confirmed", evidence, action="extend_evaluator_then_rescore")
        elif (failed_step or {}).get("stage") in ("model_configuration", "model_link"):
            item = stage("model_build", "blocked", failed_step["stage"] + "_failed", "scoring_facility",
                         "confirmed", evidence, action="repair_evaluator_then_rescore")
        elif behavior.get("status") == "model_unsupported":
            item = stage("model_build", "blocked", "model_capability_missing", "scoring_facility",
                         "confirmed", evidence, action="extend_evaluator_then_rescore")
        else:
            item = stage("model_build", "blocked", "model_build_incomplete", "unknown", "unknown",
                         evidence, action="inspect_build_diagnostics")
        stages["model_build"] = item
    cases = [case_result(c, f"/behavior/{group}/{i}")
             for group in ("model_checks", "cases", "diagnostics")
             for i, c in enumerate(behavior.get(group, []))]
    if model_compiled:
        model = [c for c in cases if c["scope"] == "internal"]
        core = [c for c in cases if c["scope"] == "core"]
        # Model failures outrank candidate mismatches. Diagnostic cases never
        # replace the core result, but remain individually visible.
        failed_model = next((c for c in model if c["outcome"] != "pass"), None)
        failures = [c for c in core if c["outcome"] != "pass"]
        if failed_model:
            item = dict(failed_model)
        elif not model or not core or (behavior.get("expected_core_count") is not None and
                                      len(core) != behavior["expected_core_count"]):
            item = stage("behavior_execution", "blocked", "required_coverage_missing", "scoring_facility",
                         "confirmed", [record("/behavior")], action="extend_evaluator_then_rescore")
        elif failures:
            # Unsupported capability / unresolved execution cannot become a
            # candidate verdict just because another case asserted first.
            item = dict(next((c for c in failures if c["responsibility"] == "scoring_facility"),
                             next((c for c in failures if c["reason_code"] == "host_deadline"),
                                  next((c for c in failures if c["responsibility"] != "candidate"), failures[0]))))
        else:
            item = stage("behavior_execution", "pass", "core_and_model_checks_passed",
                         confidence="confirmed", evidence=[record("/behavior")])
        item.pop("id", None); item.pop("scope", None)
        stages["behavior_execution"] = item
    if exception:
        name = exception["stage"]
        interrupted = exception["type"] in ("KeyboardInterrupt", "SystemExit", "EvaluationInterrupted")
        stages[name] = stage(name, "interrupted" if interrupted else "blocked",
                             "controller_interrupted" if interrupted else "stage_exception", "runtime_environment" if interrupted else "unknown",
                             "confirmed", [record("/exception")], action="inspect_execution_evidence")
        report["exception"] = exception
    if integrity_issue:
        name = "submission" if integrity_issue == "candidate_changed" else "behavior_execution"
        stages[name] = stage(name, "blocked", integrity_issue, "unknown", "confirmed",
                             [record("/candidate_integrity"), record("/reason")], action="inspect_integrity_evidence")
    # Select aggregate failures above using the original observations. Reviews
    # explain an exact observation; they cannot suppress missing coverage,
    # model failures, timeouts, or integrity failures by changing precedence.
    from benchmark.common.evaluation.reviews import matched_reviews, annotate
    try:
        reviews = matched_reviews(report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        reviews = {}
        report["review_error"] = type(error).__name__ + ": " + str(error)
        stages["behavior_execution"] = stage(
            "behavior_execution", "blocked", "review_registry_invalid", "scoring_facility", "confirmed",
            [record("/review_error")], action="repair_evaluator_then_rescore")
        report.update(status="infrastructure_error", software_status="infrastructure_error")
    cases = [annotate(case, reviews) for case in cases]
    stages["behavior_execution"] = annotate(stages["behavior_execution"], reviews)
    firmware = report.get("firmware")
    if firmware:
        stages["package"] = stage("package", "pass" if firmware.get("status") == "pass" else "blocked",
                                   "firmware_packaged" if firmware.get("status") == "pass" else "firmware_packaging_failed",
                                   "unknown", "confirmed", [record("/firmware")],
                                   action="none" if firmware.get("status") == "pass" else "package_existing_native_build")
    elif not package_requested:
        stages["package"]["reason_code"] = "packaging_not_requested"
    report.update(schema="autodriver.evaluation/v2", stages=list(stages.values()), case_results=cases)
    if report.get("software_status") == "pass":
        incomplete = next((stages[n] for n in ("submission", "native_build", "model_build", "behavior_execution")
                           if stages[n]["outcome"] != "pass"), None)
        if incomplete:
            report["software_status"] = ("model_unsupported" if incomplete["reason_code"] in
                                         ("required_coverage_missing", "model_capability_missing") else "infrastructure_error")
            if report["status"] == "pass":
                report["status"] = report["software_status"]
    failure = next((s for s in stages.values() if s["outcome"] not in ("pass", "not_run")), None)
    if failure is None and report["status"] != "pass":
        failure = stage("behavior_execution", "blocked", "unclassified_failure", "unknown", "unknown",
                        [record("/status"), record("/behavior")], action="inspect_execution_evidence")
        stages["behavior_execution"] = failure
        report["stages"] = list(stages.values())
    if failure:
        failure["causal_chain"] = failure.get("causal_chain", []) + [
            {"stage": failure["stage"], "reason_code": failure["reason_code"], "evidence": failure["evidence"]},
            {"stage": "evaluation", "reason_code": "evaluation_conclusion", "status": report["status"],
             "software_status": report.get("software_status", "not_run")},
        ]
    report["failure"] = failure
    report["result_class"] = result_class(failure)
    return report


def result_class(failure):
    if failure is None:
        return "completed"
    if failure["responsibility"] == "candidate":
        return "candidate_failure"
    if failure["responsibility"] == "scoring_facility":
        return "scoring_blocked"
    if failure["reason_code"] in ("method_wall_time", "method_cost_budget", "method_request_budget", "method_budget"):
        return "budget_terminated"
    if failure["responsibility"] in ("provider_or_transport", "runtime_environment"):
        return "infrastructure_interrupted"
    return "unresolved"


def summary_columns(report):
    """Do not invent retrospective causes for legacy reports without evidence."""
    failure = report.get("failure") or {}
    recovery = failure.get("recovery") or report.get("recovery") or {}
    if report.get("recovery", {}).get("mode") == "archive":
        # Finish local delivery before acting on an earlier generation or
        # candidate failure. Preserve that original cause in its own columns.
        recovery = report["recovery"]
    delivery = next((s for s in report.get("stages", []) if s["stage"] in ("package", "archive") and
                     s["outcome"] not in ("pass", "not_run")), {})
    return {"failure_stage": failure.get("stage", ""),
            "reason_code": failure.get("reason_code", ""),
            "responsibility": failure.get("responsibility", "unknown"),
            "confidence": failure.get("confidence", "unknown"),
            "result_class": report.get("result_class", "unclassified_legacy"),
            "recovery_action": recovery.get("action", recovery.get("mode", "")),
            "delivery_stage": delivery.get("stage", ""), "delivery_reason": delivery.get("reason_code", "")}
