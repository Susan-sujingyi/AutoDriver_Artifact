"""Method-neutral experiment lifecycle over native artifact task documents."""
import argparse
import csv
import importlib
import json
from pathlib import Path
import shutil
import sys

from benchmark.common.api import ROOT, digest, identity, inventory, load, owned, resolve_task, save, task_paths
from benchmark.common.inputs.prepare import prepare_input
from benchmark.common.source.validate import verify_source
from benchmark.common.submission import reference_submission, validate_submission, export_patch
from benchmark.common.api import apply_patch, copy_tree, relative
from benchmark.common.target.prepare import prepare_target
from benchmark.common.hardware.package import package_firmware, firmware_name, import_hardware_log
from benchmark.common.evaluation.report import explain, summary_columns

STATUSES = {"pass", "submission_rejected", "compile_failed", "behavior_failed",
            "not_completed", "model_unsupported", "infrastructure_error"}


def evaluate(task, baseline, submission, workspace, output, *, package=True):
    workspace, output = Path(workspace), Path(output)
    workspace.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "autodriver.evaluation/v1", "task_id": task.id, "status": "infrastructure_error",
              "model_calls": 0, "hardware_status": "not_run"}
    candidate_files = None
    behavior = {}
    entered = []
    exception = None
    integrity_issue = None
    current_stage = "submission"
    from benchmark.common.release import validation_identity
    report["validation_identity"] = validation_identity(task)
    try:
        entered.append("submission")
        try:
            submitted = validate_submission(task, baseline, submission, workspace / "candidate")
        except ValueError as error:
            report.update(status="submission_rejected", reason=str(error))
            return report
        candidate = workspace / "candidate"
        candidate_files = inventory(candidate)
        report["candidate_identity"] = submitted["candidate_identity"]
        current_stage = "native_build"
        entered.append(current_stage)
        backend = importlib.import_module("benchmark.common.target." + task.os + ".build")
        native = backend.build_target(task, candidate, Path(baseline) / "support", workspace / "build",
                                      submitted["candidate_identity"])
        report["native_build"] = native
        if native["status"] != "pass":
            report["status"] = native["status"] if native["status"] in STATUSES else "compile_failed"
            return report
        current_stage = "model_build"
        entered.append(current_stage)
        if task.os == "tock":
            from benchmark.common.evaluation.tock.adapter import evaluate_driver
            behavior = evaluate_driver(task, candidate / task.editable_paths[0], ROOT, workspace / "behavior")
            status = {"fail": "behavior_failed", "candidate_compile_failed": "compile_failed",
                      "execution_error": "behavior_failed", "not_exercised": "model_unsupported"}.get(behavior["status"], behavior["status"])
        else:
            from benchmark.common.evaluation.nuttx.adapter import evaluate_behavior
            behavior = evaluate_behavior(task, candidate, workspace / "behavior", submitted["candidate_identity"])
            status = behavior["status"]
        # Native compilation has already passed. Failure to compile the
        # candidate against the host model is a coverage/adapter limitation,
        # not evidence that the driver cannot compile on its target OS.
        if status == "compile_failed" and behavior.get("reason") != "public_interface_mismatch":
            status = "model_unsupported"
            report["reason"] = "Native target passed; host-model compilation is unsupported"
        if behavior.get("reason") == "public_interface_mismatch":
            report["reason"] = "public_interface_mismatch"
        report["status"] = status if status in STATUSES else "infrastructure_error"
        report["behavior"] = {k: v for k, v in behavior.items() if k not in ("evaluator_sha256",)}
        return report
    except (KeyboardInterrupt, SystemExit) as error:
        exception = {"stage": current_stage, "type": type(error).__name__, "message": str(error)}
        report.update(status="not_completed", reason=type(error).__name__ + ": " + str(error))
        raise
    except Exception as error:
        exception = {"stage": current_stage, "type": type(error).__name__, "message": str(error)}
        report.update(status="infrastructure_error", reason=type(error).__name__ + ": " + str(error))
        return report
    finally:
        # Behavior failure (including a crashed evaluator) must not suppress an
        # otherwise valid firmware. Integrity is checked on the whole candidate,
        # including immutable public support, before that independent gate.
        integrity = False
        try:
            if candidate_files is not None:
                integrity = inventory(workspace / "candidate") == candidate_files
                if not integrity:
                    integrity_issue = "candidate_changed"
                    report.update(status="submission_rejected", reason="Candidate changed after submission")
            if validation_identity(task) != report["validation_identity"]:
                integrity = False
                integrity_issue = "evaluation_inputs_changed"
                report.update(status="infrastructure_error", reason="Evaluation inputs changed during execution")
        except Exception as error:
            integrity = False
            integrity_issue = "integrity_check_failed"
            report.update(status="submission_rejected", reason="Integrity check failed: " + str(error))
        report["candidate_integrity"] = "verified" if integrity else "unverified"
        report["software_status"] = report["status"]
        report["native_build_status"] = report.get("native_build", {}).get("status", "not_run")
        explain(report, entered=entered, exception=exception, integrity_issue=integrity_issue,
                package_requested=package)
        if package and integrity and report["native_build_status"] == "pass":
            try:
                destination = output.parent / "firmware" / firmware_name(task)
                report["firmware"] = package_firmware(task, workspace / "build", report, destination)
                (destination.parent / "build.log").write_text("\n".join(
                    p.read_text(errors="replace") for p in sorted((workspace / "build").glob("*.log"))))
            except Exception as error:
                report["firmware"] = {"status": "infrastructure_error", "reason": str(error)}
                report.update(status="infrastructure_error", reason="Firmware packaging failed: " + str(error))
        logs = output / "logs"
        logs.mkdir(exist_ok=True)
        for base, prefix in ((workspace / "build", "build"), (workspace / "behavior", "behavior")):
            if base.exists():
                for path in base.glob("*.log"):
                    shutil.copyfile(path, logs / (prefix + "-" + path.name))
        cases = behavior.get("model_checks", []) + behavior.get("cases", []) + behavior.get("diagnostics", [])
        (output / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
        explain(report, entered=entered, exception=exception, integrity_issue=integrity_issue,
                package_requested=package)
        save(output / "report.json", report)
        if output.parent.parent.name in ("S", "D", "SD") and output.parent.name.isdigit():
            method = output.relative_to(ROOT / "result").parts[0]
            trial_file = output.parent / "trial.json"
            trial = load(trial_file) if trial_file.exists() else {}
            trial.update({
                "schema": "autodriver.trial/v1", "task_id": task.id, "method": method,
                "condition": output.parent.parent.name, "repeat": int(output.parent.name),
                "status": report["status"], "software_status": report["software_status"],
                "native_build_status": report["native_build_status"],
                "firmware_status": report.get("firmware", {}).get("status", "not_packaged")})
            save(trial_file, trial)


def reference_trial(task_id, batch):
    task = resolve_task(task_id)
    if len(relative(batch).parts) != 1:
        raise ValueError("A batch must be a single path component")
    run = ROOT / "run" / batch / task.id
    trial = ROOT / "result/reference" / batch / "trials" / task.id / "SD/0"
    run.mkdir(parents=True, exist_ok=False)
    trial.mkdir(parents=True, exist_ok=False)
    try:
        prepare_target(task, run / "host/baseline")
        reference_submission(task, run / "host/baseline", trial / "submission", run / "host/reference")
        record = evaluate(task, run / "host/baseline", trial / "submission", run / "host/evaluate",
                          trial / "evaluation")
        saved_trial = load(trial / "trial.json") if (trial / "trial.json").exists() else {}
        saved_trial.update({"schema": "autodriver.trial/v1", "task_id": task.id,
                                  "method": "reference", "status": record["status"],
                                  "condition": "SD", "repeat": 0})
        save(trial / "trial.json", saved_trial)
        return record
    finally:
        shutil.rmtree(run)


def archive(batch_root):
    """Derive the persistent summary from every trial, including failures."""
    batch_root = Path(batch_root)
    configuration = load(batch_root / "config.json") if (batch_root / "config.json").exists() else {}
    existing = {(p.parent.parent.parent.name, p.parent.parent.name, int(p.parent.name)): p.parent
                for p in batch_root.glob("trials/*/*/*/trial.json")}
    planned = configuration.get("planned_trials")
    if planned is not None:
        keys = [(r["task_id"], r["condition"], r["repeat"]) for r in planned]
        if len(keys) != len(set(keys)) or set(existing) - set(keys):
            raise ValueError("Duplicate planned trial or archived trial outside the frozen experiment plan")
    else:
        keys = sorted(existing)
    rows = []
    for task_id, condition, repeat in keys:
        directory = batch_root / "trials" / relative(task_id) / relative(condition) / str(repeat)
        trial = load(directory / "trial.json") if (directory / "trial.json").is_file() else {
            "task_id": task_id, "condition": condition, "repeat": repeat, "method": configuration.get("method")}
        if (directory / "evaluation/report.json").is_file():
            report = load(directory / "evaluation/report.json")
            record_state = "reported"
        else:
            historical = batch_root / "attempts" / task_id / condition / str(repeat)
            started = directory.exists() or historical.exists()
            record_state = "terminal_report_missing" if directory.exists() else "recovery_pending" if started else "not_started"
            report = {"status": "not_completed" if started else "not_started", "software_status": "not_run",
                      "hardware_status": "not_run", "result_class": "unresolved" if started else "not_started"}
        usage_file = directory / "execution/usage.json"
        usage = load(usage_file) if usage_file.exists() else {}
        rows.append({"task_id": trial["task_id"], "condition": trial["condition"],
                     "repeat": trial["repeat"], "status": report["status"],
                     "record_state": record_state,
                     "software_pass": (int(report.get("software_status", report["status"]) == "pass")
                                       if report.get("software_status", report["status"]) != "not_run" else ""),
                     "native_build_status": report.get("native_build_status", report.get("native_build", {}).get("status", "not_run")),
                     "firmware_status": report.get("firmware", {}).get("status", "not_packaged"),
                     "hardware_status": report["hardware_status"],
                     "model_requests": usage.get("requests", ""),
                     "input_tokens_known": usage.get("input_tokens", {}).get("known_total", ""),
                     "input_usage_missing": usage.get("input_tokens", {}).get("missing_requests", ""),
                     "output_tokens_known": usage.get("output_tokens", {}).get("known_total", ""),
                     "output_usage_missing": usage.get("output_tokens", {}).get("missing_requests", ""),
                     "cost_lower_cny": usage.get("cost_lower_cny", ""),
                     "cost_upper_cny": usage.get("cost_upper_cny", ""),
                     "accounting_complete": usage.get("accounting_complete", "")})
        for name in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
            rows[-1][name + "_known"] = usage.get(name, {}).get("known_total", "")
            rows[-1][name + "_missing_requests"] = usage.get(name, {}).get("missing_requests", "")
        from tests.common.execution.costs import trial_cost_columns
        rows[-1].update(trial_cost_columns(configuration, trial, usage))
        rows[-1].update(summary_columns(report))
        if configuration.get("operation") == "rescore":
            origin = report.get("rescore", {})
            rows[-1].update(operation="rescore", cost_scope="rescore_invocation_only",
                           source_batch=configuration.get("source_batch", ""), source_trial=origin.get("source_trial", ""),
                           old_status=origin.get("old_status", ""),
                           old_validation_identity=origin.get("old_validation_identity", ""),
                           new_validation_identity=origin.get("new_validation_identity", ""),
                           candidate_identity=origin.get("candidate_identity", ""),
                           score_changed=origin.get("changed", ""))
        if configuration.get("operation") == "recover":
            origin_file = directory / "recovery-origin.json"
            origin = load(origin_file) if origin_file.is_file() else {}
            rows[-1].update(operation="recover", cost_scope="recovery_attempt_only",
                           source_batch=configuration.get("source_batch", ""),
                           source_trial=origin.get("source_trial", ""),
                           retry_policy=configuration["retry_policy"], retry_policy_changed=True,
                           independent_sample=False,
                           cumulative_accounting="recovery-accounting.json")
        if record_state != "reported":
            rows[-1].update(reason_code=record_state, confidence="confirmed",
                            recovery_action="inspect_controller_state" if started else "none")
    if not rows:
        raise ValueError("No completed trials to archive")
    from tests.common.archiving import write_csv
    write_csv(batch_root / "summary.csv", rows)
    from collections import Counter
    summary = {"schema": "autodriver.experiment-summary/v2", "status": "pass",
               "csv_sha256": digest(batch_root / "summary.csv"),
               "denominator_scope": "frozen_plan" if planned is not None else "observed_trials_only",
               "planned_trials": len(keys) if planned is not None else None,
               "reported_trials": sum(r["record_state"] == "reported" for r in rows),
               "software_passes": sum(r["software_pass"] == 1 for r in rows),
               "record_states": dict(Counter(r["record_state"] for r in rows)),
               "result_classes": dict(Counter(r["result_class"] for r in rows)),
               "note": "Software scores and interruption/root-cause categories are independent. Unstarted and unresolved rows remain in the plan denominator."}
    save(batch_root / "summary.json", summary)
    return {"status": "pass", "trials": len(rows),
            "software_passes": summary["software_passes"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p = sub.add_parser("reference")
    p.add_argument("--task", required=True)
    p.add_argument("--batch", required=True)
    p = sub.add_parser("validate-source")
    p.add_argument("--task", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--task", required=True)
    p.add_argument("--condition", choices=["S", "D", "SD"], required=True)
    p.add_argument("--run", required=True)
    p = sub.add_parser("evaluate")
    p.add_argument("--task", required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--submission", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("import-patch")
    p.add_argument("--task", required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--patch", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("import-hardware-log")
    p.add_argument("--firmware", required=True)
    p.add_argument("--serial", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("run-method")
    p.add_argument("--method", choices=["autodriver"], required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--ledger")
    p = sub.add_parser("run-trial")
    p.add_argument("--method", choices=["autodriver"], required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--condition", choices=["S", "D", "SD"], required=True)
    p.add_argument("--experiment", required=True)
    p.add_argument("--batch", required=True)
    p.add_argument("--repetition", type=int, required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--ledger", required=True)
    p.add_argument("--budget-cny", type=float, default=20)
    p.add_argument("--timeout", type=int, default=1800)
    p = sub.add_parser("archive")
    p.add_argument("--batch-root", required=True)
    args = parser.parse_args(argv)
    if args.command == "list":
        result = {"tasks": sorted(task_paths()), "count": len(task_paths())}
    elif args.command == "reference":
        result = reference_trial(args.task, args.batch)
    elif args.command == "validate-source":
        from benchmark.common.source.validate import validate_source
        result = validate_source(resolve_task(args.task), owned(args.output))
    elif args.command == "prepare":
        from benchmark.common.freeze_scope import verify_scope
        verify_scope("common")
        task = resolve_task(args.task)
        verify_source(task)
        run = owned("run/" + args.run)
        prepare_target(task, run / "host/baseline")
        result = prepare_input(task, args.condition, run / "host/baseline", run / "agent")
    elif args.command == "evaluate":
        from benchmark.common.freeze_scope import verify_scope
        verify_scope("common")
        verify_source(resolve_task(args.task))
        result = evaluate(resolve_task(args.task), owned(args.baseline), owned(args.submission),
                          owned(args.workspace), owned(args.output))
    elif args.command == "import-patch":
        task = resolve_task(args.task)
        work = owned(args.workspace)
        copy_tree(owned(args.baseline) / "target", work)
        try:
            apply_patch(work, owned(args.patch))
            result = export_patch(task, owned(args.baseline), work, owned(args.output))
        finally:
            shutil.rmtree(work)
    elif args.command == "run-method":
        from benchmark.common.freeze_scope import verify_experiment_release
        from tests.common.method import run_method
        verify_experiment_release(args.method)
        result = run_method(args.method, owned(args.agent), load(owned(args.request)), owned(args.output),
                            ledger_path=owned(args.ledger) if args.ledger else None)
    elif args.command == "run-trial":
        from tests.common.trials import run_trial
        result = run_trial(method=args.method, task_id=args.task, condition=args.condition,
                           repetition=args.repetition, experiment=args.experiment, batch=args.batch,
                           model=args.model, budget={"cny": args.budget_cny, "seconds": args.timeout,
                                                     "requests": 64},
                           ledger_path=owned(args.ledger))
    elif args.command == "archive":
        result = archive(owned(args.batch_root))
    else:
        result = import_hardware_log(owned(args.firmware), owned(args.serial), owned(args.output))
    print(json.dumps(result, indent=2))
    return 0 if result.get("status", "pass") in ("pass", "complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
