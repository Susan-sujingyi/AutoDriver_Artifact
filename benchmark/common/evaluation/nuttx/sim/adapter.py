"""Frozen-OS SIM scoring: public compile, trusted model link, isolated execute.

The common NuttX adapter delegates here for all17 tasks. There is no
synthetic-header fallback; unsupported device or platform behavior remains
an explicit blocker. Release acceptance is independent of this routing.
"""
import json
from pathlib import Path
import re
import runpy
import shutil

from benchmark.common.api import ROOT, owned, save, digest, identity
from .compile import prepare_public, compile_candidate, link_model
from .build import execute, HERE


def model_directory(task):
    """An explicit shared device port may serve more than one native bus."""
    adapter = task.suite["adapters"]["nuttx/" + task.bus]
    return owned(adapter.get("sim_directory", adapter["directory"] + "/sim"))


def implementation_files(task):
    """Record the hidden evaluator inputs, not just its resulting executable."""
    directory = model_directory(task)
    paths = [p for base in (HERE, directory) for p in base.rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    paths += [owned(task.suite[name]) for name in ("oracle", "cases", "basis")]
    adapter = task.suite["adapters"]["nuttx/" + task.bus]
    if adapter.get("vectors"):
        paths.append(owned(adapter["vectors"]))
    from benchmark.common.target.interfaces import installation
    paths += [task.path, owned(task.spec["evaluation"]["suite"]),
              owned(task.spec["target"]["profile"]), owned(task.target["sanitizer"]),
              owned(task.spec["source"]["selection"]), ROOT / "third_party/sources.lock"]
    paths += [owned(p) for p in task.variant["configuration_files"]]
    paths += [owned(p) for p in installation(task.target).values()]
    paths += [ROOT / name for name in ("benchmark/common/api.py", "tests/common/sandbox.py",
              "benchmark/common/target/interfaces.py", "benchmark/common/target/nuttx/integration.py",
              "benchmark/common/source/configuration.py")]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def render_model(task):
    adapter = task.suite["adapters"]["nuttx/" + task.bus]
    directory = model_directory(task)
    if not (directory / "render.py").is_file() or not (directory / "device.c").is_file():
        raise NotImplementedError("Device has not yet been ported to frozen NuttX SIM")
    oracle = runpy.run_path(str(owned(task.suite["oracle"])))
    if not callable(oracle.get("validate_vectors")):
        raise NotImplementedError("Independent oracle validation is missing")
    checked = oracle["validate_vectors"]()
    _, vectors = oracle["render_vectors"](owned(task.suite["cases"]).parent)
    generated, cases = runpy.run_path(str(directory / "render.py"))["render"](task, oracle, vectors)
    files = {p.name: p.read_text() for p in directory.iterdir() if p.suffix in (".c", ".h")}
    files.update(generated)
    if "gpio_waveform" in task.target["board_support"]["resources"]:
        files.update({p.name: p.read_text() for p in (HERE / "wave").iterdir()
                      if p.suffix in (".c", ".h")})
    checks = [{"id": "model/numeric_oracle", "scope": "internal", "category": "model",
               "status": "pass", "details": checked}]
    return files, cases, checks


def run_cases(task, linked, cases, report, out):
    for index, case in enumerate(cases):
        name = f"case-{index}.log"
        result = execute(linked, [case["id"]], out / name)
        text = (out / name).read_text(errors="replace")
        # An ordinary nonzero exit, a named assertion, and a host deadline
        # remain different evidence. Blame is assigned by the v2 report layer.
        missing = re.search(r"BENCHSIM model_unsupported capability=([^\s]+)", text)
        unsupported = result["returncode"] == 87 and missing is not None and not result["timed_out"]
        reason = ("host_deadline" if result["timed_out"] else
                  "model_capability_missing" if unsupported else
                  "assertion_failed" if result["returncode"] == 86 and "BENCHSIM assertion" in text else
                  "case_completed" if result["returncode"] == 0 else "process_failed")
        group = {"core": "cases", "diagnostic": "diagnostics", "internal": "model_checks"}[case["scope"]]
        report[group].append({**case, **result,
                              **({"status": "model_unsupported", "missing_capability": missing[1]}
                                 if unsupported else {}),
                              "reason_code": reason, "log": name})
    if any(c["status"] != "pass" for c in report["model_checks"]):
        report["status"] = "infrastructure_error"
    elif any(c["status"] == "model_unsupported" for c in report["cases"]):
        report["status"] = "model_unsupported"
    elif any(c["timed_out"] for c in report["cases"]):
        report["status"] = "not_completed"
    elif report["cases"] and all(c["status"] == "pass" for c in report["cases"]):
        report["status"] = "pass"
    else:
        report["status"] = "behavior_failed"


def evaluate_behavior(task, candidate, out, candidate_identity):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"schema": "autodriver.behavior/v1", "backend": "frozen_nuttx_sim",
              "task_id": task.id, "candidate_identity": candidate_identity,
              "status": "infrastructure_error", "cases": [], "diagnostics": [],
              "model_checks": [], "steps": [], "model_calls": 0, "hardware_operations": 0}
    report["implementation_files"] = implementation_files(task)
    report["implementation_identity"] = identity(report["implementation_files"])
    try:
        files, cases, checks = render_model(task)
        report["model_checks"] = checks
        public, compiled, linked = (out / name for name in ("public", "candidate", "trusted"))
        configured = prepare_public(task, public)
        report["steps"].append({"stage": "model_configuration", **configured})
        if configured["status"] != "pass":
            return report
        compilation = compile_candidate(task, candidate, public, compiled)
        report["steps"].append({"stage": "candidate_compile", **compilation})
        shutil.copyfile(compiled / "compile.log", out / "candidate-compile.log")
        if compilation["status"] != "pass":
            report["status"] = compilation["status"]
            return report
        report["candidate_source_identity"] = identity(compilation["source_files"])
        report["candidate_object_sha256"] = compilation["object_sha256"]
        linkage = link_model(task, public, compiled, files, linked)
        report["steps"].append({"stage": "model_link", **linkage})
        for path in (linked / "logs").glob("*.log"):
            shutil.copyfile(path, out / ("model-" + path.name))
        if linkage["status"] != "pass":
            return report
        report["executable_sha256"] = digest(linked / "nuttx/nuttx")
        run_cases(task, linked, cases, report, out)
        return report
    except NotImplementedError as error:
        report.update(status="model_unsupported", reason=str(error))
        return report
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        return report
    finally:
        if implementation_files(task) != report["implementation_files"]:
            report.update(status="infrastructure_error", reason="evaluator_changed_during_execution")
        save(out / "report.json", report)
        with (out / "cases.jsonl").open("w") as stream:
            for case in report["model_checks"] + report["cases"] + report["diagnostics"]:
                stream.write(json.dumps(case) + "\n")
