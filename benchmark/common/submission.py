"""One patch contract for both target operating systems."""
import difflib
from pathlib import Path
import shutil

from benchmark.common.api import ROOT, apply_patch, copy_tree, digest, identity, inventory, load, owned, save
from benchmark.common.target.prepare import verify_baseline


class EmptySubmission(ValueError):
    """The verified candidate has no difference from its prepared baseline."""


class OutOfScopeSubmission(ValueError):
    """The candidate changed at least one non-editable path."""


def export_patch(task, baseline, candidate, output):
    baseline, candidate, output = Path(baseline), Path(candidate), Path(output)
    record = verify_baseline(task, baseline)
    after = inventory(candidate)
    changes = sorted(n for n in set(record["files"]) | set(after) if record["files"].get(n) != after.get(n))
    if not changes:
        raise EmptySubmission("Empty submission: no target changes")
    outside = [n for n in changes if not task.editable(n)]
    if outside:
        raise OutOfScopeSubmission("Out-of-scope submission: " + ", ".join(outside))
    patch = []
    for name in changes:
        before_path, after_path = baseline / "target" / name, candidate / name
        before = before_path.read_text().splitlines(keepends=True) if before_path.exists() else []
        text = after_path.read_text().splitlines(keepends=True) if after_path.exists() else []
        if any("\x00" in line for line in before + text):
            raise ValueError("Binary source is not allowed")
        from benchmark.common.target.naming import check_text
        check_text(name, "".join(text))
        for line in difflib.unified_diff(before, text,
                fromfile="a/" + name if before_path.exists() else "/dev/null",
                tofile="b/" + name if after_path.exists() else "/dev/null"):
            patch.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver.patch").write_text("".join(patch))
    manifest = {"schema": "autodriver.submission/v1", "task_id": task.id,
                "task_identity": record["task_identity"], "baseline_identity": record["baseline_identity"],
                "patch_sha256": digest(output / "driver.patch"), "changed_paths": changes,
                "candidate_files": {n: after.get(n) for n in changes}}
    manifest["submission_identity"] = identity(manifest)
    save(output / "manifest.json", manifest)
    return manifest


def validate_submission(task, baseline, submission, destination):
    baseline, submission, destination = map(Path, (baseline, submission, destination))
    record = verify_baseline(task, baseline)
    if any((submission / name).is_symlink() for name in ("driver.patch", "manifest.json")):
        raise ValueError("Linked submission artifacts are prohibited")
    manifest = load(submission / "manifest.json")
    unsigned = {k: v for k, v in manifest.items() if k != "submission_identity"}
    if (manifest.get("schema") != "autodriver.submission/v1" or
            manifest.get("task_id") != task.id or manifest.get("submission_identity") != identity(unsigned)
            or manifest.get("baseline_identity") != record["baseline_identity"]
            or manifest.get("task_identity") != record["task_identity"]
            or manifest.get("patch_sha256") != digest(submission / "driver.patch")):
        raise ValueError("Submission identity or baseline mismatch")
    patch = (submission / "driver.patch").read_text()
    # Reject mode changes, binary payloads and Git metadata before any apply.
    if any(line.startswith(("old mode ", "new mode ", "new file mode 120", "GIT binary patch",
                            "rename from ", "rename to ", "copy from ", "copy to "))
           for line in patch.splitlines()):
        raise ValueError("Unsupported patch operation")
    for line in patch.splitlines():
        if line.startswith(("--- ", "+++ ")):
            name = line[4:].split("\t")[0]
            if name != "/dev/null" and (not name.startswith(("a/", "b/")) or not task.editable(name[2:])):
                raise ValueError("Patch path is not editable")
    from benchmark.common.target.naming import check_text
    check_text("patch additions", "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+")))
    copy_tree(baseline / "target", destination)
    apply_patch(destination, submission / "driver.patch")
    after = inventory(destination)
    changes = sorted(n for n in set(record["files"]) | set(after) if record["files"].get(n) != after.get(n))
    if (not changes or changes != manifest["changed_paths"] or any(not task.editable(n) for n in changes)
            or manifest["candidate_files"] != {n: after.get(n) for n in changes}):
        raise ValueError("Applied patch differs from declared submission")
    return {**manifest, "candidate_identity": identity({"baseline": record["baseline_identity"],
                                                       "files": {n: after.get(n) for n in changes}})}


def reference_submission(task, baseline, output, workspace):
    workspace = Path(workspace)
    copy_tree(Path(baseline) / "target", workspace)
    try:
        for name, source in task.spec["reference"]["files"].items():
            p = workspace / name
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(owned(source), p)
        return export_patch(task, baseline, workspace, output)
    finally:
        shutil.rmtree(workspace)
