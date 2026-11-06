"""One host-owned lifecycle for final source collection and independent scoring."""
from pathlib import Path
import time
import shutil

from benchmark.common.api import ROOT, load, owned, relative, resolve_task, save
from benchmark.common.inputs.prepare import prepare_input
from benchmark.common.release import verify_experiment_release, validation_identity
from benchmark.common.evaluation.report import new_report, attach_generation, explain
from benchmark.common.source.validate import verify_source
from benchmark.common.submission import EmptySubmission, export_patch
from benchmark.common.target.prepare import prepare_target
from tests.common.method import run_method
from tests.common.pipeline import evaluate, archive
from benchmark.common.execution import interruption_handler
from tests.common.recovery import evaluation_recovery


@interruption_handler()
def run_trial(*, method, task_id, condition, repetition, experiment, batch,
              model, budget, ledger_path, knowledge=None, batch_root=None,
              previous=None, attempt=0, progress=None, control_root=None,
              generation_admission=None, release_session=None, variant='full',expected_input_identity=None):
    for component in (method, experiment, batch):
        if len(relative(component).parts) != 1:
            raise ValueError("Trial names must be single path components")
    if type(repetition) is not int or repetition < 0:
        raise ValueError("Invalid repetition")
    if method != 'autodriver' or condition != 'SD':
        raise ValueError("Unsupported method or condition")
    if method=='autodriver':
        from method.autodriver.implementation.system.variants import variant as validate_variant
        validate_variant(variant)
        if condition!='SD':raise ValueError('new autodriver live trials admit SD only')
    elif variant!='full':raise ValueError('mechanism interventions belong to autodriver only')
    release = release_session.verify() if release_session is not None else verify_experiment_release(method)
    task = resolve_task(task_id)
    verify_source(task)
    evaluation_identity = validation_identity(task)
    episode = f"{batch}-{method}-{task_id}-{condition}-{repetition}"
    episode += "-" + experiment + "-" + variant
    work = ROOT / "run" / episode
    destination = Path(batch_root) if batch_root is not None else ROOT / "result" / method / experiment / batch
    if not destination.resolve().is_relative_to((ROOT / 'result').resolve()):
        raise ValueError('Experiment output must be inside artifact result/')
    trial = destination / "trials" / task_id / condition / str(repetition)
    notify = progress or (lambda stage: None)
    work.mkdir(parents=True, exist_ok=False)
    trial.mkdir(parents=True, exist_ok=False)
    record = {"schema": "autodriver.trial/v1", "method": method, "task_id": task_id,
              "condition": condition, "repeat": repetition, "model": model,
              "release_identity": release["release_identity"], "status": "not_completed",
              "attempt": attempt}
    if method=='autodriver':record['variant']=variant
    save(trial / "trial.json", record)
    timing_started=time.time();timing_clock=time.monotonic()
    execution_started = False
    execution = None
    phase = 'prepare'
    report = None
    try:
        notify('prepare_target')
        prepare_target(task, work / "host/baseline")
        if previous is not None:
            # Reuse a completed method's immutable final patch, never feed host
            # evaluation feedback into a fresh model session.
            previous = Path(previous)
            if method=='autodriver':
                from tests.common.autodriver_submission import collect_frozen
                if collect_frozen(previous/'execution',trial/'submission',variant) is None:
                    raise ValueError('previous execution has no frozen candidate to reevaluate')
            else:shutil.copytree(previous / 'submission', trial / 'submission')
            record['execution_origin'] = str(previous.relative_to(ROOT))
            phase = 'evaluate'
            notify('evaluate_existing_patch')
            report = evaluate(task, work / 'host/baseline', trial / 'submission',
                              work / 'host/evaluate', trial / 'evaluation')
            report['recovery'] = evaluation_recovery(report)
            save(trial / 'evaluation/report.json', report)
            record.update(status=report['status'], recovery=report['recovery'])
            save(trial / 'trial.json', record)
            return report
        notify('prepare_input')
        material = prepare_input(task, condition, work / "host/baseline", work / "agent")
        if expected_input_identity is not None and material['identity']!=expected_input_identity:
            raise ValueError('prepared input differs from the frozen shared FP consumer profile')
        record["materials_identity"] = material["identity"]
        save(trial / "trial.json", record)
        if generation_admission is not None:
            # Host-only compatibility/one-attempt authorization. Nothing from
            # this callback is injected into the public request or workspace.
            notify('verify_generation_admission')
            generation_admission(task, work / "host/baseline", work / "agent", trial)
        request = {"schema": "autodriver.method-request/v1", "operation": "run",
                   "task_id": task_id, "condition": condition, "repetition": repetition,
                   "model": model, "budget": budget, "episode": episode}
        if method=='autodriver':request['variant']=variant
        execution_started = True
        phase = 'method'
        notify('run_method')
        execution = run_method(method, work / "agent", request, trial / "execution",
                               ledger_path=ledger_path, knowledge=knowledge)
        record["execution_status"] = execution["status"]
        record["method_result"] = execution.get("result")
        record["model_calls"] = execution.get("model_calls", 0)
        save(trial / "trial.json", record)
        if execution.get("cleanup", {}).get("status") != "pass":
            raise RuntimeError("Method process cleanup not verified; candidate collection refused")
        phase = 'submission'
        if execution["status"] == "submission_rejected":
            raise ValueError("Method changed immutable public materials")
        # Exactly one canonical patch, including a final candidate left by a
        # timed-out or unsuccessful method. No earlier candidate is substituted.
        termination = execution.get('termination', {})
        empty_submission = False
        try:
            if method=='autodriver':
                from tests.common.autodriver_submission import collect_frozen,no_submission_report
                frozen=collect_frozen(trial/'execution',trial/'submission',variant)
                if frozen is None:
                    report=no_submission_report(task_id,evaluation_identity,execution)
                    report['recovery']=evaluation_recovery(report)
                    save(trial/'evaluation/report.json',report);(trial/'evaluation/cases.jsonl').write_text('')
                    record.update(status=report['status'],generation_outcome=report['generation_outcome'],
                        trial_execution_complete=report['trial_execution_complete'],recovery=report['recovery'])
                    save(trial/'trial.json',record)
                    return report
                record['candidate_freeze_identity']=frozen['identity'];save(trial/'trial.json',record)
            else:export_patch(task, work / "host/baseline", work / "agent/target", trial / "submission")
        except EmptySubmission:
            # An empty target following a provider interruption is not a
            # malformed patch. Other integrity/export failures remain errors.
            if not termination.get('retryable'):
                raise
            empty_submission = True
        if termination.get('retryable'):
            # Preserve a partial patch, but do not misreport infrastructure loss
            # as driver correctness or use it to select a better candidate.
            report = new_report(task_id, evaluation_identity)
            report.update(reason=termination.get('reason'), recovery={
                'retryable': True, 'mode': termination.get('recovery_mode', 'regenerate'),
                'new_generation_allowed': termination.get('new_generation_allowed', True)})
            attach_generation(report, execution, submission_state=(
                'no_target_changes' if empty_submission else 'partial_patch_preserved'))
            save(trial / 'evaluation/report.json', report)
            (trial / 'evaluation/cases.jsonl').write_text('')
            record.update(status=report['status'], recovery=report['recovery'])
            save(trial / 'trial.json', record)
            return report
        phase = 'evaluate'
        notify('build_evaluate_package')
        report = evaluate(task, work / "host/baseline", trial / "submission",
                          work / "host/evaluate", trial / "evaluation")
        attach_generation(report, execution)
        report['recovery'] = evaluation_recovery(report)
        save(trial / 'evaluation/report.json', report)
        record.update(status=report['status'], recovery=report['recovery'])
        save(trial / 'trial.json', record)
        return report
    except BaseException as error:
        interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
        rejected = isinstance(error, ValueError) and phase == 'submission'
        status = "not_completed" if interrupted else (
            "submission_rejected" if rejected else "infrastructure_error")
        if record.get("execution_status") in ("not_completed", "fail") and not (trial / "submission").exists():
            status = "not_completed"
        report_path = trial / "evaluation/report.json"
        if interrupted and report_path.exists():
            report = load(report_path)
        else:
            report = new_report(task_id, evaluation_identity)
            report.update(status=status, reason=type(error).__name__ + ": " + str(error))
            name = 'submission' if rejected else ('model_build' if phase == 'evaluate' else 'generation')
            explain(report, entered=[name], exception=None if rejected else {
                'stage': name, 'type': type(error).__name__, 'message': str(error)})
        if execution is not None:
            attach_generation(report, execution)
        report['recovery'] = evaluation_recovery(
            report, interrupted=interrupted, exception=True,
            mode='evaluate' if phase == 'evaluate' and (trial / 'submission').exists() else 'regenerate')
        save(trial / "evaluation/report.json", report)
        if not (trial / "evaluation/cases.jsonl").exists():
            (trial / "evaluation/cases.jsonl").write_text("")
        record.update(status=report['status'], recovery=report['recovery'])
        save(trial / "trial.json", record)
        if interrupted:
            # Stop the experiment, retaining records and any final source diff.
            execution_file = trial / "execution/report.json"
            stopped = execution_file.is_file() and load(execution_file).get("cleanup", {}).get("status") == "pass"
            if stopped and (work / "agent/target").exists() and not (trial / "submission").exists():
                try:
                    export_patch(task, work / "host/baseline", work / "agent/target", trial / "submission")
                except Exception:
                    pass
            raise
        return report
    finally:
        # Timing ends at archival entry, before persistent delivery work. Both
        # methods use this same host clock; API timing remains per request.
        current=record
        current['timing']={'started_unix':timing_started,'finished_unix':time.time(),
            'seconds':max(0,time.monotonic()-timing_clock),
            'scope':'prepare, method, submission and evaluation before archival; archival excluded'}
        save(trial/'trial.json',current)
        # Persist public execution artifacts, but no duplicate candidate tree,
        # secret launch configuration, or scratch compilation output.
        from tests.common.execution.artifacts import collect
        execution_file = trial / "execution/report.json"
        safe_to_collect = not execution_started or (
            execution_file.is_file() and load(execution_file).get("cleanup", {}).get("status") == "pass")
        if safe_to_collect:
            from tests.common.archiving import finish
            try:
                finalized = finish(ROOT, trial, work, method, execution_started=execution_started,
                                   control=control_root, collector=collect, summarizer=archive)
                if isinstance(report, dict):
                    report.clear(); report.update(finalized)
            finally:
                # A failed collection keeps the workspace. Later archive
                # failures can resume from sealed collected files; never run
                # the method or evaluator merely to finish delivery.
                saved = trial / "evaluation/report.json"
                if isinstance(report, dict) and saved.is_file():
                    report.clear(); report.update(load(saved))
        else:
            # Do not race a process whose termination was not established.
            # The retained workspace also prevents a duplicate campaign run.
            save(trial / "execution/workspace-retained.json", {
                "reason": "Process cleanup not verified; no final source/log collection",
                "workspace": str(work.relative_to(ROOT))})
            archive(trial.parents[3])
