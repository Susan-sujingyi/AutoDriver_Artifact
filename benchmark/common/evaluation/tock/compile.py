"""Compile untrusted Rust separately from hidden scenarios, then link a harness."""
import json
import os
from pathlib import Path
import re
import shutil

from benchmark.common.api import ROOT, copy_tree, owned, save
from benchmark.common.target.tock.dependencies import create_cargo_environment
from tests.common.sandbox import run, tool_roots

TOOLCHAIN = "nightly-2024-01-01"


def rust_tools():
    return tool_roots(ROOT) + [p for p in (
        Path.home() / ".cargo/bin", Path.home() / ".rustup/toolchains",
        Path.home() / ".rustup/settings.toml") if p.exists()]


def isolated_run(args, cwd, log, readonly, writable, env, timeout):
    result = run(args, cwd, log, readonly=rust_tools() + readonly, writable=writable,
                 environment=env, timeout=timeout)
    return {**result, "stdout": log.read_text(errors="replace"), "stderr": "", "command": args}


def compile_candidate(task, src, work, timeout):
    public = work / "public"
    public.mkdir()
    native = public / "native"
    native.mkdir()
    for name in ("kernel", "libraries", "capsules/core"):
        copy_tree(ROOT / "third_party/tock" / name, native / name)
    manifest = (ROOT / "third_party/tock/Cargo.toml").read_text()
    manifest = re.sub(r"members\s*=\s*\[.*?\]", 'members=["kernel","capsules/core","libraries/*"]',
                      manifest, count=1, flags=re.S)
    (native / "Cargo.toml").write_text(manifest)
    shutil.copyfile(ROOT / "third_party/tock/Cargo.lock", native / "Cargo.lock")
    shim = public / "kernel"
    (shim / "src").mkdir(parents=True)
    (shim / "Cargo.toml").write_text(
        '[package]\nname="benchmark_kernel"\nversion="0.0.0"\nedition="2021"\n'
        '[lib]\nname="kernel"\n[workspace]\n[dependencies]\n'
        'tock_kernel={package="kernel",path="../native/kernel"}\n')
    extensions = task.target.get("resources", {})
    waveform = next((p for n, p in extensions.items() if n == "waveform.rs"), None)
    body = "pub use tock_kernel::*;\n"
    if waveform:
        public_hil = "\n".join(line for line in owned(waveform).read_text().splitlines()
                               if not line.startswith("//!"))
        body += "pub mod hil {pub use tock_kernel::hil::*; pub mod waveform {\n" + public_hil + "\n}}\n"
    (shim / "src/lib.rs").write_text(body)
    submission = public / "submission"
    (submission / "src").mkdir(parents=True)
    module = task.target["module"]
    flat, directory = src / (module + ".rs"), src / module
    roots = (flat.is_file(), (directory / "mod.rs").is_file())
    if sum(roots) != 1 or flat.is_symlink() or directory.is_symlink():
        raise ValueError("Expected exactly one regular candidate module root")
    if roots[0]:
        shutil.copyfile(flat, submission / "src" / flat.name)
    if directory.exists():
        copy_tree(directory, submission / "src" / module)
    (submission / "src/lib.rs").write_text("pub mod " + module + ";\n")
    (submission / "Cargo.toml").write_text(
        '[package]\nname="driver_submission"\nversion="0.0.0"\nedition="2021"\n[workspace]\n'
        '[dependencies]\nkernel={package="benchmark_kernel",path="../kernel"}\n'
        'enum_primitive={path="../native/libraries/enum_primitive"}\n'
        'tickv={path="../native/libraries/tickv"}\n'
        'capsules-core={path="../native/capsules/core"}\n')
    env, _ = create_cargo_environment(ROOT, public / "cargo-home")
    env["CARGO_TARGET_DIR"] = str(public / "compiled")
    env["CARGO_INCREMENTAL"] = "0"
    (public / "tmp").mkdir()
    env["TMPDIR"] = str(public / "tmp")
    result = isolated_run(["cargo", "+" + TOOLCHAIN, "build", "--offline", "--message-format=json",
                          "--manifest-path", str(submission / "Cargo.toml")],
                         public, work / "candidate-compile.log",
                         [ROOT / "third_party/rust_vendor"], [public], env, timeout)
    result["phase"] = "candidate_compile"
    if result["returncode"]:
        return result
    libraries = {}
    for line in result["stdout"].splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("reason") != "compiler-artifact":
            continue
        paths = [p for p in item.get("filenames", []) if p.endswith(".rlib")]
        if not paths:
            continue
        name = item["target"]["name"].replace("-", "_")
        if name == "kernel" and "benchmark_kernel" not in item["package_id"]:
            continue
        libraries[name] = paths[0]
    expected = {"kernel", "enum_primitive", "tickv", "capsules_core", "driver_submission"}
    if not expected <= set(libraries):
        raise ValueError("Candidate native link dependencies are incomplete")
    if task.spec['sensor'] == 'bme280' and task.bus == 'i2c':
        contract = src / 'public_interface.rs'
        contract.write_text("use driver_submission::bme280::Bme280;\n"
            "fn check<I: kernel::hil::i2c::I2CDevice, A: kernel::hil::time::Alarm<'static>>(\n"
            "i: &'static I, a: &'static A, b: &'static mut [u8]) { let _ = Bme280::new(i, a, b); }\n")
        args = ['rustc', '+'+TOOLCHAIN, '--edition=2021', '--crate-type=lib', str(contract),
                '-L', 'dependency='+str(public/'compiled/debug/deps'), '-o', str(work/'interface.rlib')]
        for name in sorted(expected):
            args += ['--extern', name+'='+libraries[name]]
        checked = isolated_run(args, work, work/'interface-compile.log', [], [work], env, timeout)
        checked['phase'] = 'public_interface'
        if checked['returncode']:
            return checked
    harness = (src / "main.rs").read_text()
    declaration = re.compile(r"\bmod\s+" + re.escape(module) + r"\s*;")
    harness, count = declaration.subn("use driver_submission::" + module + ";", harness)
    if count != 1:
        raise ValueError("Harness candidate module boundary changed")
    harness = harness.replace("extern crate self as kernel;", "").replace(
        "pub use tock_kernel::*;", "use kernel::*;").replace('include!("platform_hil.rs");', "")
    shutil.copyfile(ROOT/'benchmark/common/evaluation/tock/support/debug.rs', src/'test_debug.rs')
    harness += '\nmod test_debug;\n'
    harness, inserted = re.subn(r'fn\s+main\s*\(\s*\)\s*\{',
                               'fn main() { test_debug::initialize();', harness, count=1)
    if inserted != 1:
        raise ValueError('Harness main entry missing')
    (src / "main.rs").write_text(harness)
    args = ["rustc", "+" + TOOLCHAIN, "--edition=2021", str(src / "main.rs"),
            "-L", "dependency=" + str(public / "compiled/debug/deps"), "-o", str(work / "behavior")]
    for name in sorted(expected):
        args += ["--extern", name + "=" + libraries[name]]
    env["TMPDIR"] = str(work)
    result = isolated_run(args, work, work / "model-compile.log", [], [work], env, timeout)
    result["phase"] = "model_compile"
    return result


def execute_case(binary, case, work, env, timeout):
    directory = work / "execute"
    if not directory.exists():
        directory.mkdir()
        shutil.copyfile(binary, directory / "behavior")
        (directory / "behavior").chmod(0o755)
    log = work / ("case-" + case.replace("/", "_") + ".log")
    return isolated_run([str(directory / "behavior"), case], directory, log, [], [directory], env, timeout)
