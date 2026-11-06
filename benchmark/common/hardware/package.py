"""Firmware delivery and identity-bound serial log import; never probes hardware."""
import json
import math
import shutil
from pathlib import Path
import zipfile

from benchmark.common.api import digest, identity, load, save


def firmware_name(task):
    """One task-derived filename, independent of method and migration direction."""
    suffix = task.bus
    if task.bus == "spi":
        suffix += "_mode" + str(task.requirements["connection"]["spi"]["mode"])
    return f"{task.sensor}_{suffix}.zip"


def firmware_path(trial, report):
    """Read a recorded relative path; retain read-only historical compatibility."""
    trial = Path(trial)
    reuse = report.get("firmware", {}).get("reuse")
    if reuse is not None:
        # Derived scoring reports reuse an immutable, identity-bound ZIP rather
        # than duplicating old firmware or implying another native build.
        from benchmark.common.api import ROOT, owned, load, digest
        original = owned(reuse["report"])
        if not original.is_relative_to(ROOT/"result") or digest(original) != reuse["report_sha256"]:
            raise ValueError("Reused firmware report changed")
        previous = load(original)
        if previous.get("firmware", {}).get("reuse"):
            raise ValueError("Chained firmware reuse is not supported")
        if (previous.get("candidate_identity") != report.get("candidate_identity") or
                previous.get("native_build", {}).get("build_id") != report.get("native_build", {}).get("build_id")):
            raise ValueError("Reused firmware belongs to another candidate/build")
        path = firmware_path(original.parent.parent, previous)
        if digest(path) != report["firmware"]["zip_sha256"]:
            raise ValueError("Reused firmware bytes changed")
        return path
    name = report.get("firmware", {}).get("path", "firmware/firmware.zip")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or path.parts[:1] != ("firmware",):
        raise ValueError("Invalid archived firmware path")
    return trial / path


def package_firmware(task, build, evaluation, destination):
    build = Path(build)
    record = load(build / "report.json")
    if record["status"] != "pass" or evaluation["status"] == "submission_rejected":
        raise ValueError("Firmware publication requires a valid, natively built candidate")
    if evaluation.get("candidate_integrity") != "verified":
        raise ValueError("Firmware publication requires final candidate integrity verification")
    if record["candidate_identity"] != evaluation["candidate_identity"]:
        raise ValueError("Firmware and evaluated candidate differ")
    source = build / "firmware"
    if {p.name: digest(p) for p in source.iterdir()} != record["artifacts"]:
        raise ValueError("Built firmware changed")
    commands = ["ExitOnError 1", "connect", "exec SetSkipDebugDeInit = 1", "r", "h",
                "loadbin kernel.bin, 0x00000000", "verifybin kernel.bin, 0x00000000"]
    if task.os == "tock":
        commands.insert(5, "erase 0x00000000 0x000FFFFF")
        commands += ["loadbin app.bin, 0x00040000", "verifybin app.bin, 0x00040000"]
    commands += ["r", "g", "exit", ""]
    manifest = {"schema": "autodriver.firmware/v1", "task_id": task.id,
                "build_id": record["build_id"], "candidate_identity": record["candidate_identity"],
                "files": record["artifacts"], "hardware_status": "pending_validation",
                "native_build_status": "pass", "software_status": evaluation["status"],
                "sampling": task.target["sampling"], "target_os": task.os,
                "channels": task.requirements["functions"]["channels"],
                "channel_count": len(task.requirements["functions"]["channels"])}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(source.iterdir()):
            archive.write(p, p.name)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        archive.writestr("flash.jlink", "\n".join(commands))
        archive.writestr("flash-no-update.jlink", "exec DisableAutoUpdateFW\n" + "\n".join(commands))
        archive.writestr("README.md",
            "Open the serial receiver at 115200 8N1 before flashing. Sampling starts automatically.\n"
            "Run PowerShell from the extracted firmware directory:\n\n"
            "& 'D:\\Software\\JLink\\JLink_V984\\JLink.exe' -device NRF52840_XXAA -if SWD -speed 4000 "
            "-autoconnect 0 -NoGui 1 -ExitOnError 1 -CommandFile .\\flash-no-update.jlink -Log .\\flash.log\n\n"
            + ("NuttX waits 5 seconds, samples 100 rounds, then enters NSH. "
               "To sample again, send sensor_sample <count> followed by Enter.\n\n"
               if task.os == "nuttx" else "") +
            "This new firmware requires hardware validation. Compilation/software tests do not establish hardware correctness.\n")
    return {"schema": "autodriver.package/v1", "task_id": task.id, "zip_sha256": digest(destination),
            "status": "pass", "path": "firmware/" + destination.name,
            "candidate_identity": record["candidate_identity"], "hardware_status": "pending_validation"}


def import_hardware_log(firmware, serial, out):
    with zipfile.ZipFile(firmware) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    events = []
    for line in Path(serial).read_text(errors="replace").splitlines():
        try:
            events.append(json.loads(line[line.index("{"):]))
        except (ValueError, json.JSONDecodeError):
            continue
    identities = [e for e in events if e.get("event") == ("identity" if manifest["target_os"] == "nuttx" else "BEGIN")]
    ends = [e for e in events if e.get("event") == ("end" if manifest["target_os"] == "nuttx" else "END")]
    if len(identities) != 1 or len(ends) != 1:
        raise ValueError("Exactly one complete, identified run is required")
    identity_event = identities[0]
    if manifest["target_os"] == "nuttx":
        if identity_event.get("task") != manifest["task_id"] or identity_event.get("build_id") != manifest["build_id"]:
            raise ValueError("Serial log belongs to another firmware")
    elif identity_event.get("device") != manifest["task_id"] or identity_event.get("app_id") != manifest["build_id"]:
        raise ValueError("Serial log belongs to another firmware")
    errors = [e for e in events if "error" in e.get("event", "") or
              e.get("event") == "RESULT" and e.get("status") != "value"]
    count = manifest["sampling"]["rounds"]
    if manifest["target_os"] == "nuttx":
        completed = ends[0].get("client_status") == 0 and ends[0].get("attempted_samples") == count
        samples = [e for e in events if e.get("event") == "sample"]
        channels = {e.get("channel") for e in samples}
        records = [(e.get("sample"), e.get("channel")) for e in samples]
        completed = (completed and len(channels) == manifest["channel_count"]
                     and channels == set(manifest["channels"])
                     and all(isinstance(e.get("value"), (int, float)) and math.isfinite(e["value"])
                             for e in samples)
                     and len(records) == len(set(records)) == count * manifest["channel_count"]
                     and set(records) == {(i, ch) for i in range(count) for ch in channels})
    else:
        completed = ends[0].get("status") == "completed" and ends[0].get("attempted") == count * identity_event.get("channels", 0)
        samples = [e for e in events if e.get("event") == "RESULT"]
        ids = [e.get("id") for e in samples]
        completed = (completed and len(ids) == len(set(ids)) == count * manifest["channel_count"]
                     and all(e.get("callbacks") == 1 and isinstance(e.get("values"), list)
                             and all(isinstance(v, (int, float)) and math.isfinite(v) for v in e["values"])
                             for e in samples)
                     and set(ids) == set(range(1, count * manifest["channel_count"] + 1)))
    report = {"schema": "autodriver.hardware-observation/v1", "firmware_sha256": digest(firmware),
              "serial_sha256": digest(serial), "task_id": manifest["task_id"],
              "status": "pass" if not errors and completed else "behavior_failed",
              "identity": identity_event, "end": ends[0], "errors": errors,
              "scope": "User-supplied serial evidence; no automated probe operation"}
    stored = Path(out).parent / "serial.log"
    stored.parent.mkdir(parents=True, exist_ok=True)
    if stored.resolve() != Path(serial).resolve():
        if stored.exists() and digest(stored) != report["serial_sha256"]:
            raise ValueError("Use a fresh hardware observation directory for different serial evidence")
        shutil.copyfile(serial, stored)
    save(out, report)
    return report
