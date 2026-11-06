"""Native artifact data and identities. No legacy project layout or imports."""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[2]


def relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or str(p) != name:
        raise ValueError("Unsafe artifact-relative path: " + str(name))
    return Path(*p.parts)


def owned(name):
    p = ROOT / relative(name)
    if not p.resolve().is_relative_to(ROOT):
        raise ValueError("Artifact path escape: " + name)
    return p


def load(path):
    text = Path(path).read_text()
    # Source manifests contain thousands of file hashes. Parse JSON directly;
    # .yaml descriptors may themselves be JSON, so inspect the contents too.
    if text.lstrip().startswith(('{', '[')):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return yaml.safe_load(text)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def inventory(directory, *, links=False):
    directory = Path(directory)
    def entries():
        for base, dirs, names in os.walk(directory):
            for name in sorted(dirs + names):
                yield Path(base) / name

    def inspect(p):
        key = p.relative_to(directory).as_posix()
        if p.is_symlink():
            if not links or not p.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Unapproved link: " + str(p))
            return key, {"symlink": os.readlink(p)}
        if p.is_file():
            return key, {"sha256": digest(p), "executable": bool(p.stat().st_mode & 0o111)}
        return None

    # Read every byte on every invocation. Parallel I/O changes neither the
    # identity contract nor link policy, and retains no verification cache.
    with ThreadPoolExecutor(max_workers=16) as workers:
        result = dict(item for item in workers.map(inspect, entries()) if item is not None)
    return dict(sorted(result.items()))


def file_map(directory):
    return {n: v["sha256"] for n, v in inventory(directory).items()}


def copy_tree(source, destination, *, dependencies=()):
    """Fresh writable copy; never hardlink immutable inputs."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise ValueError("Use a fresh tree: " + str(destination))
    # Directory entries expose link type without a separate lstat for every
    # regular source file. Inspect every entry; never descend through links.
    pending = [source]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if entry.is_symlink():
                    p = Path(entry.path)
                    if (not p.resolve().is_relative_to(source.resolve()) and
                            not any(p.resolve().is_relative_to(Path(d).resolve()) for d in dependencies)):
                        raise ValueError("Source link escapes its snapshot: " + str(p))
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    destination.mkdir(parents=True)
    subprocess.run(["cp", "-aL", "--reflink=auto", str(source) + "/.", str(destination)], check=True)
    for p in destination.rglob("*"):
        if p.name in (".git", ".hg", ".svn", "__pycache__"):
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()


def apply_patch(target, patch, *, check=False):
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(Path(target).resolve().parent))
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    run = subprocess.run(["git", "apply", "--whitespace=nowarn",
                          *(["--check"] if check else []), str(Path(patch).resolve())],
                         cwd=target, env=env, text=True, capture_output=True)
    if run.returncode:
        raise ValueError("Patch does not apply to frozen target: " + run.stderr)


@dataclass
class Task:
    path: Path
    spec: dict
    source: dict
    variant: dict
    target: dict
    suite: dict

    @property
    def id(self):
        return self.spec["task_id"]

    @property
    def sensor(self):
        return self.spec["sensor"]

    @property
    def os(self):
        return self.target["os"]

    @property
    def bus(self):
        return self.target["bus"]

    @property
    def key(self):
        return self.sensor + ("_spi" if self.sensor == "bme280" and self.bus == "spi" else "")

    @property
    def requirements(self):
        from benchmark.common.source.configuration import effective
        return effective(self)

    @property
    def editable_paths(self):
        return self.target["editable_paths"]

    def editable(self, name):
        relative(name)
        return any(name == n or (n.endswith("/") and name.startswith(n)) for n in self.editable_paths)

    def files(self):
        result = [self.path, owned(self.spec["source"]["selection"]),
                  owned(self.spec["target"]["profile"]), owned(self.spec["evaluation"]["suite"])]
        result += [owned(n) for n in self.variant["configuration_files"]]
        result += [owned(n) for n in self.spec["inputs"]["documents"]]
        return {p.relative_to(ROOT).as_posix(): digest(p) for p in result}

    @property
    def interface_files(self):
        from benchmark.common.target.interfaces import public_interfaces
        return public_interfaces(self.target)

    def public(self, condition):
        if condition not in ("S", "D", "SD"):
            raise ValueError("Unknown input condition")
        return {
            "schema": "autodriver.public-task/v1", "task_id": self.id,
            "revision": self.spec["revision"], "condition": condition, "sensor": self.sensor,
            "requirements": self.requirements,
            "target": {"os": self.os, "board": self.target["board"], "root": "target",
                       "module": self.target.get("module"),
                       "application": "support/application" if self.os == "tock" else "support/apps/system/sensor_sample",
                       "build_command": "python support/build.py --workspace . --output output/build-1",
                       "interface": self.target.get("native_interface"),
                       "interface_files": self.interface_files,
                       "output": self.target.get("output")},
            "submission": {"format": "unified_diff", "base": "prepared_target",
                           "editable_paths": self.editable_paths},
        }


def task_paths():
    return {load(p)["task_id"]: p for p in sorted((ROOT / "benchmark").glob("*/tasks/*/task.yaml"))
            if load(p).get("schema") == "autodriver.task/v1"}


def resolve_task(task_id):
    paths = task_paths()
    if task_id not in paths:
        raise ValueError("Unknown task: " + task_id)
    path = paths[task_id]
    spec = load(path)
    required = {"schema", "task_id", "revision", "sensor", "source", "target",
                "inputs", "requirements", "reference", "evaluation"}
    if set(spec) != required or not isinstance(spec["revision"], int) or spec["revision"] < 1:
        raise ValueError("Invalid task schema")
    source = load(owned(spec["source"]["selection"]))
    variant = source["variants"][spec["source"]["variant"]]
    target = load(owned(spec["target"]["profile"]))
    suite = load(owned(spec["evaluation"]["suite"]))
    value = Task(path, spec, source, variant, target, suite)
    value.requirements  # Fail on semantic conflicts before any compilation.
    if spec["inputs"]["policy"] != "raw-s-d-sd-v1" or source["os"] not in ("riot", "zephyr"):
        raise ValueError("Unsupported input policy or source")
    if value.os not in ("tock", "nuttx") or value.bus not in ("i2c", "spi", "gpio"):
        raise ValueError("Unsupported target")
    for dest, origin in spec["reference"]["files"].items():
        if not value.editable(dest) or not owned(origin).is_file():
            raise ValueError("Invalid reference mapping")
    for name in variant["configuration_files"] + spec["inputs"]["documents"]:
        if not owned(name).is_file():
            raise ValueError("Missing task asset: " + name)
    if value.bus == "spi":
        mode = value.requirements["connection"]["spi"]["mode"]
        if mode not in (0, 3) or not value.id.endswith("-mode" + str(mode)):
            raise ValueError("SPI mode is part of task identity")
    return value
