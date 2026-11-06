"""Separate candidate compilation from hidden model compilation and linkage.

Only the sanitized, configured OS and the submitted module are readable by
the candidate compiler. The OS compiler flags come from the frozen board
Make.defs. Hidden C models and scenario data are installed in a different
tree *after* a candidate object has been accepted and identified.
"""
from pathlib import Path
import shutil

from benchmark.common.api import ROOT, copy_tree, digest, inventory, save
from tests.common.sandbox import run, tool_roots
from .build import HERE, configure, environment, prepare, build


def prepare_public(task, out):
    out = prepare(out, task)
    results = configure(out)
    report = {"status": "pass" if len(results) == 3 and
              all(s["status"] == "pass" for s in results) else "infrastructure_error",
              "steps": results}
    save(out / "configuration.json", report)
    return report


def compile_candidate(task, source, public, out):
    source, public, out = (Path(p).resolve() for p in (source, public, out))
    if not out.is_relative_to(ROOT / "run") or out == public:
        raise ValueError("Use a separate disposable candidate compilation directory")
    out.mkdir(parents=True, exist_ok=False)
    code = out / "source"
    code.mkdir()
    for name in task.editable_paths:
        original = source / name
        if original.is_symlink():
            raise ValueError("Candidate source cannot be a symlink")
        dest = code / name
        if original.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, dest)
        elif original.is_dir():
            copy_tree(original, dest)
    before = inventory(code)
    root = code / task.editable_paths[0]
    if not root.is_file():
        raise ValueError("Missing candidate translation unit")
    for name in ("home", "tmp"):
        (out / name).mkdir()
    # Filenames in these variables are host-generated. The candidate may
    # provide additional headers, but never a Makefile or compiler options.
    (out / "Compile.mk").write_text(
        '.DEFAULT_GOAL := all\n'
        'include $(TOPDIR)/Make.defs\n'
        '.PHONY: all\n'
        'all:\n'
        '\t$(CC) $(CFLAGS) -I$(dir $(SOURCE)) -c $(SOURCE) -o candidate.o\n')
    result = run(["make", "-f", "Compile.mk", "TOPDIR=" + str(public / "nuttx"),
                  "SOURCE=" + str(root)], out, out / "compile.log",
                 readonly=tool_roots(ROOT) + [public / "nuttx"], writable=[out],
                 environment=environment(out), timeout=60)
    report = {"schema": "autodriver.nuttx-sim-candidate/v1", "task_id": task.id,
              "status": "pass" if result["status"] == "pass" else "compile_failed",
              "execution": result, "source_files": before,
              "configuration_sha256": digest(public / "nuttx/.config"),
              "compiler_definitions_sha256": digest(public / "nuttx/Make.defs"),
              "model_sources_access": "denied", "network": "denied"}
    if before != inventory(code):
        report.update(status="submission_rejected", reason="candidate_changed_during_compilation")
    if report["status"] == "pass":
        report["object_sha256"] = digest(out / "candidate.o")
    save(out / "report.json", report)
    return report


def link_model(task, public, candidate, model_files, out):
    """Link the exact separately compiled object; never recompile its source."""
    from benchmark.common.api import load
    public, candidate, out = (Path(p).resolve() for p in (public, candidate, out))
    receipt = load(candidate / "report.json")
    if receipt["status"] != "pass" or digest(candidate / "candidate.o") != receipt["object_sha256"]:
        raise ValueError("Candidate object changed or did not compile")
    if not out.is_relative_to(ROOT / "run"):
        raise ValueError("Model build must be disposable")
    # Start from the immutable exports again. Configured OS trees contain
    # generated symlinks and dependency paths; copying/dereferencing those
    # would silently bind the trusted build to the public workspace.
    prepare(out, task)
    app = out / "apps/system/bench_evaluator"
    for name, content in model_files.items():
        if Path(name).name != name or Path(name).suffix not in (".c", ".h"):
            raise ValueError("Model files must be trusted C/header basenames")
        (app / name).write_text(content)
    shutil.copyfile(candidate / "candidate.o", app / "candidate.o")
    # EXTOBJS in the fixed app Makefile adds this already-compiled object.
    report = build(out)
    if (out / "nuttx/.config").is_file():
        report["configuration_sha256"] = digest(out / "nuttx/.config")
        if report["configuration_sha256"] != receipt["configuration_sha256"]:
            report.update(status="infrastructure_error", reason="candidate_model_configuration_mismatch")
    report["candidate_object_sha256"] = digest(app / "candidate.o")
    report["candidate_source_files"] = receipt["source_files"]
    report["model_files"] = {name: digest(app / name) for name in model_files}
    if report["candidate_object_sha256"] != receipt["object_sha256"]:
        report.update(status="infrastructure_error", reason="linked_object_changed")
    save(out / "link.json", report)
    return report
