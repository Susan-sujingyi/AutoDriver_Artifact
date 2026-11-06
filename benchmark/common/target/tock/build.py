"""Native Tock kernel and shared application from a validated candidate."""
import os
from pathlib import Path
import shutil

from benchmark.common.api import ROOT, copy_tree, digest, identity, save
from benchmark.common.execution import run_logged
from benchmark.common.target.tock.dependencies import create_cargo_environment
from benchmark.common.hardware import dependencies
from benchmark.common.hardware.elf2tab import tools_binary
from benchmark.common.hardware.formats import kernel_binary, validate_images
from benchmark.common.applications.tock.profile import CHANNELS, rust_config
from benchmark.common.evaluation.tock.compile import rust_tools
from tests.common.sandbox import run
from benchmark.common.release import validation_identity


def build_target(task, candidate, support, out, candidate_identity):
    out.mkdir(parents=True, exist_ok=False)
    work = out / "workspace"
    work.mkdir()
    copy_tree(candidate, work / "target")
    firmware = out / "firmware"
    firmware.mkdir()
    build_id = identity({"task": identity(task.files()), "candidate": candidate_identity,
                         "support": validation_identity(task),
                         "builder": digest(Path(__file__))})
    report = {"schema": "autodriver.native-build/v1", "task_id": task.id,
              "build_id": build_id, "candidate_identity": candidate_identity,
              "status": "infrastructure_error", "steps": [], "hardware_status": "not_run"}

    def command(args, cwd, log, env, timeout=600):
        if log == "kernel-build.log":
            result = run(args, cwd, out / log, readonly=rust_tools() + [ROOT / "third_party/rust_vendor"],
                         writable=[work], environment=env, timeout=timeout)
        else:
            result = run_logged(args, cwd, out / log, timeout=timeout, environment=env)
        report["steps"].append({"log": log, **result})
        if result["status"] != "pass":
            report["status"] = "not_completed" if result.get("timed_out") else "compile_failed"
            raise RuntimeError("Native build failed: " + log)

    try:
        seed = {k: os.environ[k] for k in ("PATH", "HOME", "RUSTUP_HOME", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
        env, _ = create_cargo_environment(ROOT, work / "kernel-home", environment=seed)
        (work / "tmp").mkdir()
        env["TMPDIR"] = str(work / "tmp")
        env["CARGO_INCREMENTAL"] = "0"
        command(["make"], work / "target/boards/nordic/nrf52840dk", "kernel-build.log", env)
        kernel = work / "target/target/thumbv7em-none-eabi/release/nrf52840dk"
        shutil.copyfile(kernel, firmware / "kernel.elf")
        (firmware / "kernel.bin").write_bytes(kernel_binary(kernel))
        profile = {"device": task.id, "app_id": build_id, "profile_id": identity(task.target),
                   **task.target["sampling"], "channels": []}
        for name in task.requirements["functions"]["channels"]:
            driver, cmd, unit, kind = CHANNELS[name]
            profile["channels"].append({"name": name, "driver": driver, "command": cmd, "unit": unit, "kind": kind})
        rust_config(profile, work / "profile.rs")
        deps = dependencies.verify(ROOT)
        env = dependencies.environment(ROOT, work / "app-home")
        env.update(CARGO_TARGET_DIR=str(work / "app-target"), LIBTOCK_PLATFORM="nrf52840",
                   DRIVERBENCH_PROFILE_RS=str(work / "profile.rs"),
                   RUSTFLAGS="-C relocation-model=static -C link-arg=-icf=all --remap-path-prefix=" + str(ROOT) + "=/artifact")
        command(["cargo", "build", "--locked", "--offline", "--release", "--target", "thumbv7em-none-eabi",
                 "--manifest-path", str(ROOT / "benchmark/common/applications/tock/Cargo.toml")],
                ROOT, "app-build.log", env)
        shutil.copyfile(work / "app-target/thumbv7em-none-eabi/release/driverbench-hardware", firmware / "app.elf")
        tool = tools_binary(ROOT, deps, out)
        command([str(tool), "--deterministic", "--kernel-major", "2", "--kernel-minor", "1",
                 "--stack", "8192", "--app-heap", "1024", "--kernel-heap", "1024", "--minimum-footer-size", "256",
                 "--supported-boards", "nrf52840dk", "-n", task.id, "-o", str(firmware / "app.tab"),
                 str(firmware / "app.elf") + ",cortex-m4"], ROOT, "elf2tab.log", env)
        validate_images(firmware / "kernel.elf", firmware / "kernel.bin", firmware / "app.elf",
                        firmware / "app.tbf", firmware / "app.tab")
        (firmware / "app.tbf").rename(firmware / "app.bin")
        report.update(status="pass", artifacts={p.name: digest(p) for p in firmware.iterdir()},
                      profile=profile, layout="validated")
    except RuntimeError as error:
        report["error"] = str(error)
    finally:
        save(out / "report.json", report)
        shutil.rmtree(work)
    return report
