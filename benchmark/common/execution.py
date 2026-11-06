"""Bounded local subprocesses and interruption evidence for offline v2 scoring.

This is lifecycle management, not a filesystem or network sandbox.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid


class EvaluationInterrupted(KeyboardInterrupt):
    pass


@contextmanager
def interruption_handler():
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise EvaluationInterrupted('SIGTERM received')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def save(path: Path, data: dict) -> None:
    """Publish whole JSON records; never leave a truncated successful report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def compile_lock(cache: Path, timeout=300):
    deadline = time.monotonic()+timeout
    with (cache/'.driverbench.lock').open('a') as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Build cache lock wait exceeded supervisor limit')
                time.sleep(min(0.05, max(0, deadline-time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def terminate_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def run(command, cwd, timeout, env):
    start = time.monotonic()
    process = None
    timed_out = False
    try:
        with interruption_handler():
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, errors='replace',
                                       start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_group(process)
                stdout, stderr = process.communicate(timeout=2)
            return {'command':command, 'returncode':process.returncode, 'stdout':stdout,
                    'stderr':stderr, 'elapsed_seconds':round(time.monotonic()-start,3),
                    'timed_out':timed_out}
    finally:
        if process is not None:
            terminate_group(process)


def run_logged(command, cwd, log, timeout=300, *, environment):
    value = run(command, cwd, timeout, environment)
    log.write_text(value['stdout']+value['stderr'])
    missing = any(s in value['stdout']+value['stderr'] for s in (
        'no matching package named', 'is not installed', '--offline was specified'))
    return {'status':'not_completed' if value['timed_out'] else 'pass' if value['returncode']==0
            else 'not_run_infra' if missing else 'fail',
            'returncode':value['returncode'], 'seconds':value['elapsed_seconds'],
            'timed_out':value['timed_out'], 'log':str(log)}
