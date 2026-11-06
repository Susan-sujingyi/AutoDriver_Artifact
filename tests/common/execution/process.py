"""Host supervisor with Landlock and fixed-gateway descriptor injection."""
import json
import os
import signal
from pathlib import Path
import socket
import subprocess
import sys
import time

from benchmark.common.api import ROOT
from benchmark.common.execution import interruption_handler, save
from .transport import FixedGatewayBroker, receive_listener


# Cleanup is host work, not additional method time. The subreaper has 30s to
# kill/reap descendants; leave a separate margin for scheduling and publication.
CLEANUP_GRACE_SECONDS = 60
FORCED_EXIT_SECONDS = 10


def stop_worker(process):
    """Bounded shutdown without replacing the original timeout with wait()."""
    if process.poll() is not None:
        return {"status": "exited", "returncode": process.returncode}
    process.terminate()
    try:
        process.wait(timeout=CLEANUP_GRACE_SECONDS)
        return {"status": "reaped", "returncode": process.returncode}
    except subprocess.TimeoutExpired:
        # A stuck subreaper is an infrastructure failure. Never claim cleanup
        # passed just because its process group received SIGKILL: descendants
        # may have created independent sessions.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=FORCED_EXIT_SECONDS)
        except subprocess.TimeoutExpired:
            return {"status": "unverified", "reason": "worker_survived_sigkill"}
        return {"status": "unverified", "reason": "forced_worker_exit",
                "returncode": process.returncode}


def receive_cleanup(fd):
    """Read only the trusted subreaper's private, bounded pipe message."""
    os.set_blocking(fd, False)
    try:
        payload = os.read(fd, 4097)
    except BlockingIOError:
        payload = b''
    if not payload:
        return {"status": "missing"}
    try:
        if len(payload) > 4096:
            raise ValueError("Oversized cleanup receipt")
        record = json.loads(payload)
        if not isinstance(record, dict) or record.get('status') not in ('pass', 'infrastructure_error'):
            raise ValueError("Invalid cleanup receipt")
        return record
    except (ValueError, UnicodeError) as error:
        return {"status": "invalid", "error_type": type(error).__name__}


@interruption_handler()
def execute(command, *, cwd, readonly, writable, environment, output,
            gateway_port=None, timeout=1800, stdin=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    config = {"command": command, "cwd": str(cwd),
              "readonly": [str(p) for p in readonly], "writable": [str(p) for p in writable],
              "environment": environment}
    # Bootstrap config contains only the disposable gateway token, never the
    # provider key. Kept host-side and removed after the child has read it.
    request = output / ".launch.json"
    save(request, config)
    request.chmod(0o600)
    args = [sys.executable, "-m", "tests.common.execution.worker", "--config", str(request)]
    parent = child = broker = process = None
    cleanup_read = cleanup_write = None
    started = time.monotonic()
    timed_out = False
    result = {"status": "infrastructure_error"}
    try:
        cleanup_read, cleanup_write = os.pipe()
        args += ["--cleanup-fd", str(cleanup_write)]
        if gateway_port is not None:
            parent, child = socket.socketpair()
            args += ["--control-fd", str(child.fileno())]
        with (output / "stdout.jsonl").open("w") as stdout, (output / "stderr.log").open("w") as stderr:
            process = subprocess.Popen(
                args, cwd=ROOT,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(ROOT),
                     "PYTHONDONTWRITEBYTECODE": "1"},
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, start_new_session=True, close_fds=True,
                pass_fds=((child.fileno(),) if child is not None else ()) + (cleanup_write,))
            os.close(cleanup_write)
            cleanup_write = None
            if child is not None:
                child.close()
                child = None
                broker = FixedGatewayBroker(receive_listener(parent), gateway_port, max_sockets=2048)
                request.unlink()  # listener proves bootstrap consumed the config
            try:
                process.communicate(input=stdin.encode() if stdin is not None else None, timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                # Stop exactly once in finally. Keep deadline evidence even
                # when shutdown fails, and never extend the generation turn.
            result.update(returncode=process.returncode, timed_out=timed_out,
                          status="not_completed" if timed_out else
                          "pass" if process.returncode == 0 else "fail")
    except BaseException as error:
        result.update(status="not_completed" if isinstance(error, (KeyboardInterrupt, SystemExit))
                      else "infrastructure_error", reason=type(error).__name__)
        raise
    finally:
        if process is not None:
            # The dedicated subreaper owns descendants across session changes.
            shutdown = stop_worker(process)
            result["shutdown"] = shutdown
            result["returncode"] = process.returncode
            result["timed_out"] = timed_out
            cleanup = receive_cleanup(cleanup_read)
            # Persist after the worker has exited. Slow host filesystem I/O
            # cannot interrupt the subreaper while it publishes its evidence.
            save(output / "cleanup.json", cleanup)
            result["cleanup"] = cleanup
            if cleanup["status"] != "pass" or shutdown["status"] == "unverified":
                result["cleanup"] = dict(cleanup, status="infrastructure_error")
                result.update(status="infrastructure_error", reason="Method process cleanup not verified")
        request.unlink(missing_ok=True)
        if broker is not None:
            broker.close()
            result["transport"] = broker.report()
        if parent is not None:
            parent.close()
        if child is not None:
            child.close()
        if cleanup_read is not None:
            os.close(cleanup_read)
        if cleanup_write is not None:
            os.close(cleanup_write)
        result.update(seconds=round(time.monotonic() - started, 3),
                      boundary="landlock+seccomp", network="fixed_gateway" if gateway_port else "denied")
        save(output / "process.json", result)
    return result
