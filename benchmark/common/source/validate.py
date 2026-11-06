"""Release-only source compilation and independently verifiable receipts."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import yaml

from benchmark.common.api import ROOT, digest, identity, inventory, load, owned, save
from benchmark.common.target.prepare import snapshot, verify_snapshot
from tests.common.sandbox import run, tool_roots


def source_identity(task):
    selection = owned(task.spec["source"]["selection"])
    validation = owned(task.variant["validation"])
    files = {selection.relative_to(ROOT).as_posix(): digest(selection)}
    for path in task.variant["configuration_files"]:
        files[path] = digest(owned(path))
    for entry in task.variant["selected_files"]:
        actual = digest(owned(entry["path"]))
        if actual != entry["sha256"]:
            raise ValueError("Frozen source selected file changed: " + entry["path"])
        files[entry["path"]] = actual
    for path in validation.rglob("*"):
        if path.is_file() and path.name != "receipt.json":
            files[path.relative_to(ROOT).as_posix()] = digest(path)
    files[Path(__file__).relative_to(ROOT).as_posix()] = digest(Path(__file__))
    for name in ("benchmark/common/source/configuration.py", "third_party/snapshots.yaml",
                 "dependencies/requirements.lock"):
        files[name] = digest(owned(name))
    registry = load(ROOT / "third_party/snapshots.yaml")
    for name in (task.source["snapshot"],):
        manifest = owned(registry[name]["manifest"])
        files[manifest.relative_to(ROOT).as_posix()] = digest(manifest)
    if task.source["os"] == "zephyr":
        directory = ROOT / "benchmark/common/source/zephyr/fixture"
        files.update({str((directory / n).relative_to(ROOT)): v["sha256"]
                      for n, v in inventory(directory).items()})
    else:
        for name in ("cmsis", "nrfx", "mpaland-printf"):
            path = ROOT / "third_party/metadata/releases" / (name + ".json")
            files[path.relative_to(ROOT).as_posix()] = digest(path)
    for dep in task.variant.get("dependencies", []):
        if dep.get("manifest"):
            files[dep["manifest"]] = digest(owned(dep["manifest"]))
    return {"files": files, "identity": identity(files), "variant": task.spec["source"]["variant"]}


def verify_source(task):
    receipt = load(owned(task.variant["validation"]) / "receipt.json")
    current = source_identity(task)
    if receipt.get("status") != "pass" or receipt.get("input_identity") != current["identity"]:
        raise ValueError("Source release receipt mismatch; explicit republication required")
    return receipt


def validate_source(task, out):
    """Explicit maintenance entry point. Never called by the experiment runner."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    initial = source_identity(task)
    snapshot_identity = verify_snapshot(task.source["snapshot"])
    work = out / "workspace"
    work.mkdir()
    for name in ("home", "tmp"):
        (work / name).mkdir()
    env = {"PATH": str(ROOT / ".venv/bin") + ":/usr/bin:/bin",
           "HOME": str(work / "home"), "TMPDIR": str(work / "tmp"),
           "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1", "GIT_CONFIG_NOSYSTEM": "1"}
    source = snapshot(task.source["snapshot"])[0]
    fixture = owned(task.variant["validation"])
    readonly = tool_roots(ROOT) + [source, fixture]
    if task.source["os"] == "riot":
        app = work / "source"
        app.mkdir()
        shutil.copytree(fixture, app / "validation", ignore=shutil.ignore_patterns("receipt.json"))
        for name in task.variant["configuration_files"]:
            shutil.copyfile(owned(name), app / Path(name).name)
        for name, folder, variable in (
                ("cmsis", "cmsis-5.9.0", "CMSIS"),
                ("nrfx", "nrfx-3.14.0-riot-nrf52", "NRF5X_NRFX_MDK"),
                ("mpaland-printf", "mpaland-printf-4.0.0-riot", "MPALAND_PRINTF")):
            path = ROOT / "third_party/libraries" / folder
            manifest = load(ROOT / "third_party/metadata/releases" / (name + ".json"))
            if {n: v["sha256"] for n, v in inventory(path).items()} != manifest["files"]:
                raise ValueError("Source dependency drift: " + name)
            readonly.append(path)
            env["PKG_SOURCE_LOCAL_" + variable] = str(path)
            if name == "nrfx":
                dest = work / "global-build/nrf5x_nrfx_mdk/nrf52"
                shutil.copytree(path, dest)
                (dest / ".prepared").touch()
        commands = [["make", "-j4", "BOARD=nrf52840dk", "RIOTBASE=" + str(source),
                     "BUILD_DIR=" + str(work / "global-build"), "BINDIRBASE=" + str(work / "bin"),
                     "PKGDIRBASE=" + str(work / "packages"), "WERROR=1", "all"]]
        cwd = app / "validation"
    else:
        fixture_root = ROOT / "benchmark/common/source/zephyr/fixture"
        readonly.append(fixture_root)
        # The validator supplies only OS/specific source materials, never refs.
        readonly += [owned(task.spec["source"]["selection"]).parent]
        readonly += [owned(f["path"]).parent for f in task.variant["selected_files"]]
        modules = [str(fixture_root)]
        for dep in task.variant.get("dependencies", []):
            if dep.get("path"):
                path = owned(dep["path"])
                readonly.append(path)
                if (path / "zephyr/module.yml").exists():
                    modules.append(str(path))
        config = load(owned(next(n for n in task.variant["configuration_files"] if n.endswith(".yaml"))))
        template = (fixture / "validation.conf").read_text()
        lines = [line for line in template.splitlines()
                 if not any(line.startswith(k + "=") for k in config)]
        # Derived hidden Kconfig symbols are assertions about the result, not
        # assignable inputs. Preserve explicit n entries from the reviewed
        # configuration; verify all false expectations after configuration.
        lines += [k + "=" + ("y" if v is True else "n" if v is False else str(v))
                  for k, v in config.items() if k.startswith("CONFIG_")
                  and (v is not False or re.search("^" + k + "=", template, re.M))]
        (work / "source.conf").write_text("\n".join(lines) + "\n")
        build = work / "build"
        env["ZEPHYR_TOOLCHAIN_VARIANT"] = "host"
        commands = [["cmake", "-S", str(fixture_root), "-B", str(build), "-G", "Ninja",
            "-DBOARD=native_sim/native/64", "-DSOURCE_BUILD_DEVICE=" + task.key,
            "-DARTIFACT_ROOT=" + str(ROOT), "-DCONF_FILE=" + str(work / "source.conf"),
            "-DDTC_OVERLAY_FILE=" + str(owned(next(n for n in task.variant["configuration_files"] if n.endswith(".overlay")))),
            "-DBUILD_VERSION=autodriver-source-release", "-DUSE_CCACHE=0",
            "-DPython3_EXECUTABLE=" + sys.executable, "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
            "-DZEPHYR_MODULES=" + ";".join(modules), "-DWEST=WEST-NOTFOUND",
            "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF", "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
            "-DUSER_CACHE_DIR=" + str(work / "cache")],
            ["cmake", "--build", str(build), "--parallel", "4"]]
        cwd = work
    report = {"schema": "autodriver.source-validation/v1", "source_os": task.source["os"],
              "variant": initial["variant"], "input_identity": initial["identity"], "snapshot_identity": snapshot_identity,
              "status": "infrastructure_error", "steps": [], "model_calls": 0, "hardware_operations": 0}
    try:
        for i, command in enumerate(commands):
            log = out / f"step-{i}.log"
            execution = run(command, cwd, log, readonly=list(set(readonly)), writable=[work],
                            environment=env, timeout=900)
            report["steps"].append({"command": command, **execution, "log_sha256": digest(log)})
            if execution["status"] != "pass":
                report["status"] = "not_completed" if execution["timed_out"] else "compile_failed"
                return report
        if task.source["os"] == "zephyr":
            actual = (work / "build/zephyr/.config").read_text()
            for key, value in config.items():
                if key.startswith("CONFIG_"):
                    expected = "y" if value is True else str(value)
                    if (value is False and re.search("^" + key + "=", actual, re.M)
                            or value is not False and key + "=" + expected not in actual.splitlines()):
                        raise ValueError("Compiled source configuration conflict: " + key)
            commands_log = load(work / "build/compile_commands.json")
            primary = owned(next(f["path"] for f in task.variant["selected_files"] if f.get("role") == "primary_source"))
            if sum(Path(e["file"]).resolve() == primary for e in commands_log) != 1:
                raise ValueError("Exact selected source was not compiled")
            report["source_compiled_sha256"] = digest(primary)
        if source_identity(task) != initial:
            raise ValueError("Source inputs changed during release validation")
        report["status"] = "pass"
        receipt = {k: report[k] for k in ("schema", "source_os", "variant", "input_identity", "status")}
        receipt.update(compiler=subprocess.check_output(
            ["arm-none-eabi-gcc" if task.source["os"] == "riot" else "cc", "--version"], text=True).splitlines()[0],
            validation_record=out.relative_to(ROOT).as_posix() + "/report.json",
            scope="Native configuration, selected driver and API link; not target behavior or hardware")
        save(fixture / "receipt.json", receipt)
        return report
    finally:
        save(out / "report.json", report)
        shutil.rmtree(work)
