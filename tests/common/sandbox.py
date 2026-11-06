"""Fail-closed Linux file-access and network boundary for offline subprocesses.

Landlock restricts access in the kernel across exec and fork. It does not hide
path metadata or create PID/mount namespaces. No host /proc, home, project root,
Git store or private evaluator directory is granted. Tests must prove denied
reads, including absolute paths and traversal, rather than infer confinement
from a directory layout. The seccomp layer blocks network and process-memory
escape routes. Only host tool directories and explicit material roots are read.
"""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import sys
import time

from benchmark.common.execution import terminate_group


class Ruleset(ctypes.Structure):
    _fields_ = [('handled_access_fs', ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [('allowed_access', ctypes.c_uint64), ('parent_fd', ctypes.c_int32)]


class ArgumentRule(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_uint),
                ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]


def boundary(readonly: list[Path], writable: list[Path]):
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise RuntimeError('Offline boundary requires Linux with Landlock')
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 3:
        raise RuntimeError('Landlock ABI >= 3 is required; no unsandboxed fallback')
    seccomp = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                             ctypes.c_int, ctypes.c_uint,
                                             ctypes.POINTER(ArgumentRule)]
    roots = [(p.resolve(strict=True), False) for p in readonly]
    roots += [(p.resolve(strict=True), True) for p in writable]
    roots.append((Path('/dev/null'), True))
    if any(str(p) == '/' for p, _ in roots):
        raise ValueError('Cannot grant filesystem root')

    def enter():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS
            raise OSError(ctypes.get_errno(), 'no_new_privs')
        all_rights = (1 << 15) - 1  # ABI 3: includes REFER and TRUNCATE
        rule = Ruleset(all_rights)
        descriptor = libc.syscall(444, ctypes.byref(rule), ctypes.sizeof(rule), 0)
        if descriptor < 0:
            raise OSError(ctypes.get_errno(), 'landlock_create_ruleset')
        try:
            for path, write in roots:
                fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rights = all_rights if write else (1 | 4 | 8)
                    if not path.is_dir():
                        rights &= 1 | 2 | 4 | (1 << 14)
                    entry = PathRule(rights, fd)
                    if libc.syscall(445, descriptor, 1, ctypes.byref(entry), 0):
                        raise OSError(ctypes.get_errno(), 'landlock_add_rule')
                finally:
                    os.close(fd)
            if libc.syscall(446, descriptor, 0):
                raise OSError(ctypes.get_errno(), 'landlock_restrict_self')
        finally:
            os.close(descriptor)
        context = seccomp.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
        if not context:
            raise RuntimeError('seccomp_init failed')
        try:
            # Rust std uses an anonymous AF_UNIX socket pair to report exec
            # errors to its parent. It cannot connect to a host socket/network.
            # Permit only that local pair; all socket/connect/bind calls remain
            # denied, as do socketpair calls for every other address family.
            pair_rule = ArgumentRule(0, 1, 1, 0)  # arg0 != AF_UNIX (SCMP_CMP_NE)
            if seccomp.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM,
                    seccomp.seccomp_syscall_resolve_name(b'socketpair'), 1, ctypes.byref(pair_rule)):
                raise RuntimeError('seccomp socketpair rule failed')
            for name in ('socket', 'connect', 'bind', 'listen',
                         'accept', 'accept4', 'ptrace', 'process_vm_readv',
                         'process_vm_writev', 'pidfd_getfd', 'open_by_handle_at',
                         'mount', 'umount2', 'pivot_root', 'chroot', 'setns',
                         'unshare', 'bpf', 'mknod', 'mknodat', 'io_uring_setup', 'io_uring_enter',
                         'io_uring_register'):
                number = seccomp.seccomp_syscall_resolve_name(name.encode())
                if number >= 0 and seccomp.seccomp_rule_add(
                        context, 0x00050000 | errno.EPERM, number, 0):
                    raise RuntimeError('seccomp_rule_add failed: ' + name)
            if seccomp.seccomp_load(context):
                raise RuntimeError('seccomp_load failed')
        finally:
            seccomp.seccomp_release(context)

    return enter, int(abi)


def tool_roots(root: Path) -> list[Path]:
    paths = [Path(p) for p in ('/usr', '/bin', '/lib', '/lib64',
                              '/etc/ld.so.cache', '/etc/ld.so.conf',
                              '/etc/ld.so.conf.d', '/dev/null', '/dev/urandom')]
    paths += [root / '.venv', root / 'third_party/tools']
    # A venv can link to a system Python outside /usr (this environment uses
    # /opt/conda). Grant its interpreter and standard libraries, never its home
    # or the artifact containing the venv.
    paths += [Path(sys._base_executable).resolve(), Path(sys.base_prefix) / 'lib']
    return [p for p in paths if p.exists()]


def run(command: list[str], cwd: Path, log: Path, *, readonly: list[Path],
        writable: list[Path], environment: dict, timeout: int = 600) -> dict:
    enter, abi = boundary(readonly, writable)
    start = time.monotonic()
    process = None
    timed_out = False
    try:
        # Log descriptors are opened by the supervisor; subprocesses receive no
        # open reference files, directory handles, or inherited socket handles.
        with log.open('w') as stream:
            process = subprocess.Popen(command, cwd=cwd, env=environment,
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, close_fds=True,
                                       start_new_session=True, preexec_fn=enter)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_group(process)
                code = process.returncode
    finally:
        if process is not None:
            terminate_group(process)
    return {'returncode': code, 'timed_out': timed_out,
            'seconds': round(time.monotonic() - start, 3),
            'boundary': 'landlock+seccomp', 'landlock_abi': abi,
            'network': 'denied', 'status': 'not_completed' if timed_out else
            'pass' if code == 0 else 'fail'}
