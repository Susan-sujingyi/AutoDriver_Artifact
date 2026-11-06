"""Public scratch build helper. No reference, evaluator, or host imports."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

# This file is copied into immutable public support. A normal direct Python
# invocation must not add bytecode files beside its imported support module.
sys.dont_write_bytecode = True
import yaml
from native_commands import nuttx_configuration


def delegated_build(workspace,output,url):
    import urllib.request
    from urllib.error import HTTPError
    from urllib.parse import urlsplit
    parsed=urlsplit(url)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path!='/v1/public-build' or parsed.query or parsed.fragment or parsed.username or parsed.password or not parsed.port:
        raise ValueError('fixed host public compile endpoint required')
    if workspace!=Path(__file__).resolve().parent.parent:
        raise ValueError('host public compile uses this prepared workspace only')
    output.mkdir(parents=True,exist_ok=False)
    request=urllib.request.Request(url,data=json.dumps({'schema':'autodriver.public-build-request/v1','operation':'compile'}).encode(),
        headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['AUTODRIVER_GATEWAY_TOKEN']})
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request,timeout=605) as response:record=json.load(response)
    except HTTPError as error:
        data=json.loads(error.read(65536));(output/'host-build-error.json').write_text(json.dumps(data,indent=2))
        raise RuntimeError(data.get('reason','host public compile refused')) from None
    if record.get('schema')!='autodriver.public-build-response/v1' or record.get('producer')!='approved_compile_tool':
        raise ValueError('invalid public compile response')
    results=[dict(result,host_status=record['status']) for result in record['commands']]
    (output/'report.json').write_text(json.dumps(results,indent=2))
    (output/'host-build.json').write_text(json.dumps(record,indent=2))
    for i,result in enumerate(results):(output/('step-'+str(i)+'.log')).write_text(result['stderr'])
    return results


def build(workspace, output):
    workspace, output = Path(workspace).resolve(), Path(output).resolve()
    if not output.is_relative_to(workspace / "output"):
        raise ValueError("Build scratch must be under output/")
    url=os.environ.get('AUTODRIVER_PUBLIC_BUILD_URL')
    if url:return delegated_build(workspace,output,url)
    task = yaml.safe_load((workspace / "task.yaml").read_text())
    output.mkdir(parents=True, exist_ok=False)
    scratch = output / "workspace"
    scratch.mkdir()
    shutil.copytree(workspace / "target", scratch / "target")
    env = dict(os.environ)
    for name in ("home", "tmp"):
        (scratch / name).mkdir()
    env.update(HOME=str(scratch / "home"), TMPDIR=str(scratch / "tmp"),
               PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    if task["target"]["os"] == "tock":
        cargo = scratch / "cargo"
        cargo.mkdir()
        vendor = Path(env["AUTODRIVER_RUST_VENDOR"]).resolve(strict=True)
        (cargo / "config.toml").write_text(
            '[net]\noffline=true\n[source.crates-io]\nreplace-with="bundled"\n'
            '[source.bundled]\ndirectory=' + json.dumps(str(vendor)) + "\n")
        env.update(CARGO_HOME=str(cargo), CARGO_NET_OFFLINE="true", CARGO_INCREMENTAL="0")
        commands = [["make"]]
        cwd = scratch / "target/boards/nordic/nrf52840dk"
    else:
        shutil.copytree(workspace / "support/apps", scratch / "apps")
        (scratch / "apps/system/sensor_sample/run_identity.h").write_text(
            "#define SENSOR_SAMPLE_TASK_ID " + json.dumps(task["task_id"]) + "\n"
            '#define SENSOR_SAMPLE_BUILD_ID "public-compile-only"\n')
        bus = next(b for b in ("i2c", "spi", "gpio") if b in task["requirements"]["connection"])
        _, _, commands = nuttx_configuration(bus, task["sensor"], automatic_startup=False)
        cwd = scratch / "target"
    results = []
    try:
        for i, command in enumerate(commands):
            started = time.monotonic()
            log = output / f"step-{i}.log"
            with log.open("w") as stream:
                process = subprocess.Popen(command, cwd=cwd, env=env,
                                           stdout=stream, stderr=subprocess.STDOUT)
                try:
                    code = process.wait(timeout=300)
                    timed_out = False
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    code, timed_out = -1, True
            record = {"command": command, "returncode": code, "timed_out": timed_out,
                      "seconds": round(time.monotonic() - started, 3),
                      "stdout": "", "stderr": log.read_text(errors="replace")}
            results.append(record)
            if code:
                break
    finally:
        (output / "report.json").write_text(json.dumps(results, indent=2))
        shutil.rmtree(scratch)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = build(args.workspace, args.output)
    raise SystemExit(0 if result and all(r["returncode"] == 0 and r.get("host_status","pass")=="pass" for r in result) else 1)
