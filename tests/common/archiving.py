"""Resumable host-only trial finalization. Never runs a method or evaluator."""
from contextlib import contextmanager
from copy import deepcopy
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from benchmark.common.api import ROOT, digest, identity, inventory, load, relative
from benchmark.common.evaluation.report import stage, result_class
from .execution.artifacts import collect, collection_sources, read_regular


class ArchivePending(RuntimeError):
    """Durable trial evidence remains available for explicit archive recovery."""


def atomic_bytes(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def write_csv(path, rows):
    if not rows:
        raise ValueError("No rows to archive")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    atomic_bytes(path, stream.getvalue().encode())


@contextmanager
def lock(trial):
    with (trial / "execution/archive.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArchivePending("An archive writer still owns this trial") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def inside(root, path, directory):
    root, path = Path(root).resolve(), Path(path).absolute()
    if not path.is_relative_to(root / directory) or not path.resolve().is_relative_to(root / directory):
        raise ValueError("Archive path must remain inside " + directory)
    for item in (path, *path.parents):
        if item == root:
            break
        if item.is_symlink():
            raise ValueError("Linked archive paths are forbidden")
    return path


def source_files(work, method):
    # The shared allowlist includes both output/ and .method-state/. Bind
    # names to their common agent root, not to the former output-only root.
    # These keys are provenance, never paths to reopen without the allowlist.
    public = work / "agent/output"
    return {str(p.relative_to(public.parent)): {"sha256": hashlib.sha256(read_regular(p)).hexdigest(), "destination": name}
            for p, name in collection_sources(public, method)}


def immutable_files(trial):
    omitted = {"trial.json", "evaluation/report.json", "execution/archive-state.json", "execution/archive.lock",
               "execution/events.jsonl", "execution/workspace-retained.json"}
    temporary_prefixes = ("trial.json.", "evaluation/report.json.", "execution/archive-state.json.")
    return {name: value for name, value in inventory(trial).items()
            if name not in omitted and not name.startswith("execution/method/") and
            not (name.endswith(".tmp") and name.startswith(temporary_prefixes))}


def normalize_report(report, state):
    report = deepcopy(report)
    for name, field in state["base_fields"].items():
        if field["present"]:
            report[name] = deepcopy(field["value"])
        else:
            report.pop(name, None)
    if "stages" in report:
        report["stages"] = [deepcopy(state["base_archive_stage"]) if s["stage"] == "archive" else s for s in report["stages"]]
    return report


def update_report(trial, state, *, success, error=None):
    report = normalize_report(load(trial / "evaluation/report.json"), state)
    if report.get("schema") == "autodriver.evaluation/v2":
        item = stage("archive", "pass" if success else "blocked",
                     "archive_completed" if success else "archive_interrupted", "runtime_environment", "confirmed",
                     [{"path": "../execution/archive-state.json"}],
                     action="none" if success else "resume_archive", prerequisites=() if success else ("unchanged_archive_inputs", "method_cleanup_verified"))
        if error:
            item["error_type"] = type(error).__name__
        report["stages"] = [item if s["stage"] == "archive" else s for s in report["stages"]]
        if not success and report.get("failure") is None:
            report["failure"] = item
            report["result_class"] = result_class(item)
    if not success:
        report["status"] = "infrastructure_error"
        report["recovery"] = {"retryable": True, "mode": "archive", "new_generation_allowed": False}
    save(trial / "evaluation/report.json", report)
    row = load(trial / "trial.json")
    row["status"] = report["status"]
    if "recovery" in report:
        row["recovery"] = report["recovery"]
    else:
        row.pop("recovery", None)
    save(trial / "trial.json", row)
    return report


def verify_inputs(trial, state):
    report = normalize_report(load(trial / "evaluation/report.json"), state)
    if identity(report) != state["report_identity"] or immutable_files(trial) != state["immutable_files"]:
        raise ArchivePending("Archived evaluation, submission, firmware or execution evidence changed")
    row = load(trial / "trial.json")
    row = {k:v for k,v in row.items() if k not in ("status", "recovery")}
    if identity(row) != state["trial_identity"]:
        raise ArchivePending("Trial identity changed during archiving")


def finish(root, trial, work, method, *, execution_started, control=None, collector=None, summarizer=None):
    """Called only after evaluation/generation has returned or been stopped."""
    root = Path(root).resolve()
    trial, work = inside(root, trial, "result"), inside(root, work, "run")
    (trial / "execution").mkdir(exist_ok=True)
    path = trial / "execution/archive-state.json"
    with lock(trial):
        if path.exists():
            state = load(path)
        else:
            execution = trial / "execution/report.json"
            if execution_started and (not execution.is_file() or load(execution).get("cleanup", {}).get("status") != "pass"):
                raise ArchivePending("Method cleanup is unverified; workspace retained")
            report = load(trial / "evaluation/report.json")
            row = load(trial / "trial.json")
            if row.get("method") != method:
                raise ArchivePending("Archive method does not match the original trial")
            stat = work.stat()
            state = {"schema": "autodriver.archive/v1", "status": "pending", "phase": "collect",
                     "trial": str(trial.relative_to(root)), "workspace": str(work.relative_to(root)), "method": method,
                     "control": str(inside(root, control, "result").relative_to(root)) if control else None,
                     "execution_started": execution_started, "work_inode": [stat.st_dev, stat.st_ino],
                     "archiver_sha256": digest(Path(__file__)),
                     "retain_workspace": report.get("recovery", {}).get("mode") in (
                         "replay_response", "inspect_receipt", "inspect_client_checkpoint"),
                     "report_identity": identity(report), "trial_identity": identity({k:v for k,v in row.items() if k not in ("status", "recovery")}),
                     "base_fields": {k:{"present": k in report, "value": report.get(k)} for k in ("status", "failure", "result_class", "recovery")},
                     "base_archive_stage": next((s for s in report.get("stages", []) if s["stage"] == "archive"), None),
                     "immutable_files": immutable_files(trial), "source_root": "agent",
                     "source_files": source_files(work, method), "attempts": []}
            save(path, state)
        if (state.get("schema") != "autodriver.archive/v1" or state["trial"] != str(trial.relative_to(root)) or
                state["workspace"] != str(work.relative_to(root)) or state["method"] != method or
                state.get("archiver_sha256") != digest(Path(__file__))):
            raise ArchivePending("Archive recovery identity mismatch")
        verify_inputs(trial, state)
        if state["status"] == "complete":
            if (digest(trial / "evaluation/report.json") != state["completed_report_sha256"] or
                    digest(trial / "trial.json") != state["completed_trial_sha256"]):
                raise ArchivePending("Completed archive report changed")
            return load(trial / "evaluation/report.json")
        attempt = {"started": time.time(), "phase": state["phase"]}
        state["attempts"].append(attempt)
        save(path, state)
        try:
            if state["phase"] == "collect":
                if source_files(work, method) != state["source_files"]:
                    raise ArchivePending("Method logs changed after archive admission")
                (collector or collect)(work / "agent/output", trial / "execution", method)
                if source_files(work, method) != state["source_files"]:
                    raise ArchivePending("Method logs changed during collection")
                for value in state["source_files"].values():
                    if digest(trial / "execution" / value["destination"]) != value["sha256"]:
                        raise ArchivePending("Collected method log differs from its admitted source")
                state["collected_files"] = {n:v for n,v in inventory(trial / "execution").items()
                                             if n.startswith("method/") or n == "events.jsonl"}
                state["phase"] = "cleanup"
                save(path, state)
            collected = {n:v for n,v in inventory(trial / "execution").items()
                         if n.startswith("method/") or n == "events.jsonl"}
            if collected != state["collected_files"]:
                raise ArchivePending("Collected archive changed before completion")
            verify_inputs(trial, state)
            if work.exists():
                stat = work.stat()
                if [stat.st_dev, stat.st_ino] != state["work_inode"]:
                    raise ArchivePending("Workspace was replaced; refusing deletion")
                if not state.get("retain_workspace"):
                    shutil.rmtree(work)
                else:
                    save(trial / "execution/workspace-retained.json", {
                        "reason": "Original method checkpoint required for local response recovery; no new generation authorized",
                        "workspace": state["workspace"], "cleanup_verified": True})
            state["phase"] = "summary"
            save(path, state)
            report = update_report(trial, state, success=True)
            if summarizer is None:
                from .pipeline import archive
                summarizer = archive
            summarizer(trial.parents[3])
            verify_inputs(trial, state)
            state.update(status="complete", phase="complete")
            state["completed_report_sha256"] = digest(trial / "evaluation/report.json")
            state["completed_trial_sha256"] = digest(trial / "trial.json")
            attempt.update(status="pass", finished=time.time())
            save(path, state)
            return report
        except BaseException as error:
            attempt.update(status="interrupted", error_type=type(error).__name__, message=str(error), finished=time.time())
            state["status"] = "pending"
            save(path, state)
            update_report(trial, state, success=False, error=error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ArchivePending("Archive remains recoverable at " + state["trial"] + ": " + str(error)) from error


def recover(trial, *, root=ROOT):
    """Explicit maintenance operation; never invokes generation or scoring."""
    root = Path(root).resolve()
    trial = inside(root, trial, "result")
    state = load(trial / "execution/archive-state.json")
    def run():
        return finish(root, trial, root / relative(state["workspace"]), state["method"],
                      execution_started=state["execution_started"], control=root / relative(state["control"]) if state.get("control") else None)
    if state.get("control"):
        from .campaign import campaign_lock
        with campaign_lock(inside(root, root / relative(state["control"]), "result")):
            return run()
    return run()


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", required=True, help="Unfinished trial directory relative to artifacts/")
    args = parser.parse_args(argv)
    report = recover(ROOT / relative(args.trial))
    print(json.dumps({"status": report["status"], "software_status": report.get("software_status"), "model_calls": 0}))


if __name__ == "__main__":
    main()
