"""Public S/D/SD inputs; no hidden fixtures or evaluation records."""
import json
from pathlib import Path
import shutil
import yaml

from benchmark.common.api import ROOT, copy_tree, digest, identity, inventory, owned, save
from benchmark.common.target.prepare import snapshot, verify_baseline, verify_public_support
from benchmark.common.inputs.documents import prepare_views


def prepare_input(task, condition, baseline, output):
    if condition not in ("S", "D", "SD"):
        raise ValueError("Unknown condition")
    baseline, output = Path(baseline), Path(output)
    receipt = verify_baseline(task, baseline)
    output.mkdir(parents=True, exist_ok=False)
    copy_tree(baseline / "target", output / "target")
    if (baseline / "support").exists():
        copy_tree(baseline / "support", output / "support")
    public = task.public(condition)
    public["submission"]["baseline_identity"] = receipt["baseline_identity"]
    if condition in ("S", "SD"):
        copy_tree(snapshot(task.source["snapshot"])[0], output / "source_os")
        selected = []
        # Upstream files stay in the OS copy; approved historical processed
        # selections remain separate and their role/provenance is explicit.
        for entry in task.variant["selected_files"]:
            src = owned(entry["path"])
            if digest(src) != entry["sha256"]:
                raise ValueError("Frozen source selection drift")
            if entry["path"].startswith("third_party/" + task.source["os"] + "/"):
                path = "source_os/" + entry["path"].split("/", 2)[2]
            else:
                selection_root = owned(task.spec["source"]["selection"]).parent
                # Keep the declared hierarchy: CCS811 has both an archived
                # header and a compatibility header with the same basename.
                path = ("source_inputs/" + src.relative_to(selection_root).as_posix()
                        if src.is_relative_to(selection_root)
                        else "source_inputs/selected/" + src.name)
                dest = output / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists() and digest(dest) != digest(src):
                    raise ValueError("Selected source basename collision")
                shutil.copyfile(src, dest)
            selected.append({"path": path, "role": entry.get("role", "source")})
        configs = []
        for name in task.variant["configuration_files"]:
            dest = output / "source_inputs" / Path(name).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(owned(name), dest)
            configs.append(dest.relative_to(output).as_posix())
        public["source"] = {"os": task.source["os"], "selected_files": selected,
                            "configuration_files": configs,
                            "authenticity": task.variant.get("authenticity", task.source.get("authenticity",
                                "Frozen selection; selected files may include disclosed historical mechanical processing."))}
        for dependency in task.variant.get("dependencies", []):
            path = dependency.get("path")
            if path and owned(path).is_dir():
                copy_tree(owned(path), output / "source_deps" / Path(path).name)
    if condition in ("D", "SD"):
        (output / "documents").mkdir()
        for name in task.spec["inputs"]["documents"]:
            shutil.copyfile(owned(name), output / "documents" / Path(name).name)
        prepare_views(output)
    from method.common.interfaces import read_interfaces
    read_interfaces(output, public)
    (output / "task.yaml").write_text(yaml.safe_dump(public, sort_keys=False))
    (output / "output").mkdir()
    (output / "prompt.md").write_text(
        "Implement the sensor driver described in task.yaml in target/.\n"
        "Use only the provided materials. Preserve the fixed public interface, board wiring and application.\n"
        "Modify only submission.editable_paths in target/. Do not create a patch: the host collects "
        "the final source changes into the canonical submission after you finish. "
        "Do not modify the task or public support files. Build in a scratch copy under output/ "
        "to keep build products out of target/.\n"
        "The host performs independent validation after submission.\n")
    verify_public_support(task, output)
    files = inventory(output)
    manifest = {"schema": "autodriver.materials/v1", "task_id": task.id, "condition": condition,
                "baseline_identity": receipt["baseline_identity"], "files": files, "identity": identity(files)}
    save(output / "materials.json", manifest)
    return manifest
