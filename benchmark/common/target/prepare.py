"""Prepare immutable target inputs directly from the native task/profile."""
from pathlib import Path
import shutil

from benchmark.common.api import ROOT, apply_patch, copy_tree, digest, identity, inventory, load, owned, save
from benchmark.common.policies.materials import sanitize_target


def snapshot(name):
    entry = load(ROOT / "third_party/snapshots.yaml")[name]
    return owned(entry["root"]), load(owned(entry["manifest"]))


def verify_snapshot(name):
    tree, manifest = snapshot(name)
    files = manifest["files"]
    if isinstance(files, list):
        files = {f["path"]: f["sha256"] for f in files}
    actual = inventory(tree, links=True)
    expected = {}
    for key, val in files.items():
        expected[key] = val if isinstance(val, dict) else {"sha256": val}
    mismatches = [k for k in set(actual) | set(expected)
                  if k not in actual or k not in expected or
                  any(actual[k].get(field) != value for field, value in expected[k].items()
                      if field in ("sha256", "symlink", "executable"))]
    if mismatches:
        raise ValueError("Snapshot drift: " + name + ": " + repr(mismatches[:5]))
    return identity(actual)


def copy_public_libtock(source, destination):
    """Keep the userspace library, without its unrelated embedded kernel.

    The public kernel is target/. QEMU runner workflows are not supported by
    this benchmark. Never modify the frozen dependency or replace the omitted
    tree with a link to it.
    """
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for entry in sorted(source.iterdir()):
        if entry.name in ("tock", ".git", ".hg", ".svn", "__pycache__"):
            continue
        if entry.is_symlink():
            raise ValueError("Unexpected public libtock root link: " + str(entry))
        if entry.is_dir():
            copy_tree(entry, destination / entry.name)
        else:
            shutil.copy2(entry, destination / entry.name)


def verify_public_support(task, path):
    """Constant-time boundary check, alongside existing inventory validation."""
    embedded = Path(path) / "support/libtock-rs/tock"
    if task.os == "tock" and (embedded.exists() or embedded.is_symlink()):
        raise ValueError("Public libtock-rs must not contain an embedded tock tree")


def prepare_target(task, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    source, _ = snapshot(task.os)
    if task.os == "tock":
        target = out / "target"
        copy_tree(source, target)
        before_target = inventory(target)
        for name in task.target.get("remove_paths", []):
            p = target / name
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        if task.target.get("board_patch"):
            apply_patch(target, owned(task.target["board_patch"]))
        sanitize_target(target, task.target["module"])
        # The same fixed upper application is available to every method.
        support = out / "support"
        support.mkdir()
        application = support / "application"
        copy_tree(owned(task.target["application"]), application)
        copy_public_libtock(ROOT / "third_party/libtock-rs", support / "libtock-rs")
        cargo = application / "Cargo.toml"
        cargo.write_text(cargo.read_text().replace("../../../../third_party/libtock-rs", "../libtock-rs"))
        for name in ("profile.py", "__init__.py"):
            (application / name).unlink(missing_ok=True)
    else:
        from benchmark.common.target.nuttx.integration import (
            apply_recipe, audit_clean_tree, integrate_platform, integrate_public)
        stage = out / ".prepare"
        stage.mkdir()
        copy_tree(source, stage / "nuttx", dependencies=[snapshot("nuttx-apps")[0]])
        copy_tree(snapshot("nuttx-apps")[0], stage / "apps")
        recipe = load(owned(task.target["sanitizer"]))
        apply_recipe(stage, recipe)
        cleaned = audit_clean_tree(stage, recipe)
        before_target = {n[len("nuttx/"):]: v for n, v in cleaned.items() if n.startswith("nuttx/")}
        before_apps = {n[len("apps/"):]: v for n, v in cleaned.items() if n.startswith("apps/")}
        spec = {"device_key": task.key, "bus": task.bus,
                "configuration": {"connection": task.requirements["connection"][task.bus]},
                "target_profile": task.target}
        # Sanitization must remove the old implementation before installing a public ABI.
        for name in task.editable_paths:
            if (stage / "nuttx" / name).exists():
                raise ValueError("Unexpected candidate destination already exists: " + name)
        integrate_platform(ROOT, stage, spec)
        integrate_public(ROOT, stage, spec)
        (stage / "nuttx").rename(out / "target")
        (out / "support").mkdir()
        (stage / "apps").rename(out / "support/apps")
        stage.rmdir()
    for template, name in (("public_build.py", "build.py"), ("commands.py", "native_commands.py")):
        shutil.copyfile(ROOT / "benchmark/common/target" / template, out / "support" / name)
    from benchmark.common.target.naming import check_changes
    files = inventory(out / "target")
    check_changes(out / "target", before_target, files)
    if task.os == "nuttx":
        check_changes(out / "support/apps", before_apps, inventory(out / "support/apps"))
    verify_public_support(task, out)
    support = inventory(out / "support") if (out / "support").exists() else {}
    record = {"schema": "autodriver.baseline/v1", "task_id": task.id,
              "task_identity": identity(task.files()), "files": files,
              "support_files": support, "baseline_identity": identity({"target": files, "support": support})}
    save(out / "baseline.json", record)
    return record


def verify_baseline(task, path):
    path = Path(path)
    verify_public_support(task, path)
    record = load(path / "baseline.json")
    if record["task_id"] != task.id or record["task_identity"] != identity(task.files()):
        raise ValueError("Wrong task baseline")
    support = inventory(path / "support") if (path / "support").exists() else {}
    if record["files"] != inventory(path / "target") or record["support_files"] != support:
        raise ValueError("Prepared baseline was modified")
    return record
