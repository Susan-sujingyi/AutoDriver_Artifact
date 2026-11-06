"""Trusted per-invocation subreaper; cleans descendants even after setsid/fork."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from benchmark.common.execution import interruption_handler

REAP_SECONDS = 30


def children():
    result = set()
    for path in Path("/proc/self/task").glob("*/children"):
        result.update(int(pid) for pid in path.read_text().split())
    return result


def reap():
    # Kill parents first: all remaining descendants, including double-forked
    # session leaders, are then adopted by this dedicated host subreaper.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    started = time.monotonic()
    deadline = started + REAP_SECONDS
    killed = set()
    while True:
        current = children()
        if not current:
            return {"status": "pass", "reaped_children": len(killed),
                    "seconds": round(time.monotonic() - started, 3)}
        for pid in current:
            try:
                os.kill(pid, signal.SIGKILL)
                killed.add(pid)
            except ProcessLookupError:
                pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
            except ChildProcessError:
                break
        if time.monotonic() >= deadline:
            remaining = sorted(children())
            if not remaining:
                continue
            states = {}
            for pid in remaining[:16]:
                try:
                    states[str(pid)] = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
                except (FileNotFoundError, ProcessLookupError):
                    states[str(pid)] = 'disappeared'
            return {"status": "infrastructure_error", "remaining_children": remaining[:16],
                    "remaining_children_count": len(remaining),
                    "child_states": states, "seconds": round(time.monotonic() - started, 3)}
        time.sleep(.01)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--control-fd", type=int)
    parser.add_argument("--cleanup-fd", required=True, type=int)
    args = parser.parse_args()
    lib = ctypes.CDLL(None, use_errno=True)
    if lib.prctl(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Cannot supervise detached method children")
    command = [sys.executable, "-m", "tests.common.execution.launcher", "--config", args.config]
    if args.control_fd is not None:
        command += ["--control-fd", str(args.control_fd)]
    code = 130
    try:
        with interruption_handler():
            process = subprocess.Popen(command, pass_fds=(args.control_fd,) if args.control_fd is not None else ())
            if args.control_fd is not None:
                os.close(args.control_fd)
            code = process.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        # Do not allow a second stop signal to interrupt cleanup/publication.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        cleanup = reap()
        # Not inherited by launcher/agent (Popen pass_fds above excludes it).
        # A single PIPE_BUF-sized message cannot wait on disk or a full pipe.
        payload = json.dumps(cleanup).encode()
        if len(payload) > 4096:
            payload = b'{"status":"infrastructure_error","reason":"oversized_cleanup"}'
        try:
            if os.write(args.cleanup_fd, payload) != len(payload):
                raise RuntimeError("Incomplete cleanup publication")
        finally:
            os.close(args.cleanup_fd)
    raise SystemExit(code if code >= 0 and cleanup["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
