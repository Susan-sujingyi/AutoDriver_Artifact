"""Build the frozen NuttX SIM in a fresh, host-only disposable workspace.

The OS is never edited in third_party. Only its host clock peripheral is
replaced; kernel services and the Sensor upper half remain the frozen code.
This module is not a fallback to the former synthetic-header environment.
"""
import os
from pathlib import Path
import shutil

from benchmark.common.api import ROOT, copy_tree, digest, save, load, owned
from tests.common.sandbox import run, tool_roots

HERE = Path(__file__).resolve().parent


def prepare(out, task=None):
    out = Path(out).resolve()
    if not out.is_relative_to(ROOT / "run"):
        raise ValueError("SIM workspaces must be under artifacts/run")
    out.mkdir(parents=True, exist_ok=False)
    copy_tree(ROOT / "third_party/nuttx", out / "nuttx",
              dependencies=[ROOT / "third_party/nuttx-apps"])
    copy_tree(ROOT / "third_party/nuttx-apps", out / "apps")
    if task is not None:
        from benchmark.common.target.nuttx.integration import apply_recipe
        from benchmark.common.target.interfaces import install_interfaces
        apply_recipe(out, load(owned(task.target["sanitizer"])))
        install_interfaces(ROOT, out / "nuttx", task.target)
    copy_tree(HERE / "app", out / "apps/system/bench_evaluator")
    clock = out / "nuttx/arch/sim/src/sim/posix/sim_hosttime.c"
    shutil.copyfile(HERE / "hosttime.c", clock)
    for name in ("home", "tmp", "logs"):
        (out / name).mkdir()
    return out


def environment(out):
    return {"PATH": os.pathsep.join([
        str(ROOT / "third_party/tools/kconfig-frontends/usr/bin"),
        str(ROOT / ".venv/bin"), "/usr/bin", "/bin"]),
        "HOME": str(out / "home"), "TMPDIR": str(out / "tmp"),
        "PYTHON": str(ROOT / ".venv/bin/python"),
        "LC_ALL": "C", "LANG": "C", "SOURCE_DATE_EPOCH": "0",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}


def configure(out):
    """Generate real OS configuration and headers before candidate compilation."""
    out = Path(out).resolve()
    steps = [["bash", "tools/configure.sh", "-E", "-l", "-a", "../apps", "sim:nsh"],
             ["make", "olddefconfig"], ["make", "context"]]
    results = []
    for index, command in enumerate(steps):
        if index == 1:
            shutil.copyfile(HERE / "defconfig", out / "nuttx/.config")
            definitions = out / "nuttx/boards/sim/sim/sim/scripts/Make.defs"
            marker = '# Independent evaluator: pass-through Sensor observation.\n'
            content = definitions.read_text()
            if marker not in content:
                definitions.write_text(content + '\n' + marker +
                    # SIM resolves OS/app references in its partial link;
                    # final host linkage is too late to wrap those calls.
                    'LDLINKFLAGS += --wrap=sensor_register --wrap=sensor_unregister '
                    '--wrap=sensor_custom_register --wrap=sensor_custom_unregister\n')
        result = run(command, out / "nuttx", out / f"logs/configure-{index}.log",
                     readonly=tool_roots(ROOT), writable=[out],
                     environment=environment(out), timeout=600)
        results.append({"command": command, **result})
        if result["status"] != "pass":
            break
    return results


def build(out):
    out = Path(out).resolve()
    report = {"schema": "autodriver.nuttx-sim-build/v1", "status": "running",
              "clock_sha256": digest(HERE / "hosttime.c"),
              "configuration_sha256": digest(HERE / "defconfig"),
              "steps": [], "model_calls": 0, "hardware_operations": 0}
    report["steps"] = configure(out)
    if any(step["status"] != "pass" for step in report["steps"]):
        report["status"] = "infrastructure_error"
        save(out / "build.json", report)
        return report
    for command in [["make", "-j4"]]:
        result = run(command, out / "nuttx", out / "logs/build.log",
                     readonly=tool_roots(ROOT), writable=[out],
                     environment=environment(out), timeout=600)
        report["steps"].append({"command": command, **result})
        if result["status"] != "pass":
            report["status"] = "infrastructure_error"
            break
    else:
        config = (out / "nuttx/.config").read_text()
        required = ("ARCH_SIM", "SIM_WALLTIME_SIGNAL", "SENSORS", "SPI_EXCHANGE",
                    "SYSTEM_BENCH_EVALUATOR", "DEBUG_ASSERTIONS", "BOARD_CRASHDUMP_CUSTOM",
                    "ARCH_HAVE_UDELAY")
        missing = [name for name in required if f"CONFIG_{name}=y\n" not in config]
        if missing or "CONFIG_SIM_WALLTIME_SLEEP=y\n" in config:
            report.update(status="infrastructure_error", configuration_missing=missing)
        else:
            report.update(status="pass", executable_sha256=digest(out / "nuttx/nuttx"))
    save(out / "build.json", report)
    return report


def execute(out, arguments, log):
    # Never grant the linked process access to model sources, vectors,
    # candidate compilation trees, other results, or the public OS tree.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="execute-", dir=out) as name:
        workspace = Path(name)
        binary = workspace / "nuttx"
        shutil.copyfile(out / "nuttx/nuttx", binary)
        binary.chmod(0o755)
        for item in ("home", "tmp"):
            (workspace / item).mkdir()
        return run([str(binary), *arguments], workspace, log,
                   readonly=tool_roots(ROOT), writable=[workspace],
                   environment=environment(workspace), timeout=30)


def platform_contract(out):
    out = prepare(out)
    shutil.copyfile(HERE / "platform_contract.c",
                    out / "apps/system/bench_evaluator/platform_contract.c")
    report = build(out)
    if report["status"] == "pass":
        report["contract"] = execute(out, [], out / "logs/platform-contract.log")
        report["status"] = report["contract"]["status"]
        log = out / "logs/assertion-contract.log"
        failure = execute(out, ["--assert-contract"], log)
        failure["contract_status"] = ("pass" if failure["returncode"] == 86
            and not failure["timed_out"] and "BENCH_EXPECTED_ASSERTION" in log.read_text() else "fail")
        report["assertion_contract"] = failure
        if failure["contract_status"] != "pass":
            report["status"] = "fail"
        log = out / "logs/unsupported-contract.log"
        unsupported = execute(out, ["--unsupported-contract"], log)
        unsupported["contract_status"] = ("pass" if unsupported["returncode"] == 87
            and not unsupported["timed_out"] and
            "BENCHSIM model_unsupported capability=contract_probe" in log.read_text() else "fail")
        report["unsupported_contract"] = unsupported
        if unsupported["contract_status"] != "pass":
            report["status"] = "fail"
    save(out / "platform.json", report)
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform-contract", type=Path, required=True)
    args = parser.parse_args()
    result = platform_contract(args.platform_contract)
    print(result["status"], flush=True)
    raise SystemExit(0 if result["status"] == "pass" else 1)
