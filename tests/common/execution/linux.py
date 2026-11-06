"""Linux/x86-64 process restrictions, applied before executing an agent.

Filesystem rules cover the entire process tree. The executor denies all native
TCP connect/bind operations. A separate host broker can inject streams connected
to one fixed local model gateway. Port restrictions alone are not an IP allowlist.
"""
from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import platform


READ = (1 << 0) | (1 << 2) | (1 << 3)
WRITE = READ | (1 << 1) | (1 << 4) | (1 << 5) | (1 << 7) | (1 << 8) | (1 << 12) | (1 << 13) | (1 << 14)
ALL_FS_V3 = (1 << 15) - 1


class Ruleset(ctypes.Structure):
    _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("access", ctypes.c_uint64), ("fd", ctypes.c_int32)]


def libc():
    if platform.machine() != "x86_64":
        raise RuntimeError("This executor currently supports Linux x86-64 only")
    lib = ctypes.CDLL(None, use_errno=True)
    lib.syscall.restype = ctypes.c_long
    return lib


def checked(result):
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def abi_version() -> int:
    return checked(libc().syscall(444, ctypes.c_void_p(), ctypes.c_size_t(0), 1))


def restrict_files(read_paths: list[str], write_paths: list[str], *, deny_native_tcp=True):
    lib = libc()
    if abi_version() < 4:
        raise RuntimeError("Landlock ABI >= 4 is required; refusing unsandboxed fallback")
    # Agent launchers always use the default. A relocated host fixture can
    # retain its local mock HTTP server while still denying old-project reads.
    rules = Ruleset(ALL_FS_V3, 3 if deny_native_tcp else 0)
    fd = checked(lib.syscall(444, ctypes.byref(rules), ctypes.sizeof(rules), 0))
    try:
        for names, access in ((read_paths, READ), (write_paths, WRITE)):
            for name in names:
                path = Path(name).resolve(strict=True)
                path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
                try:
                    rights = access if path.is_dir() else access & ((1 << 0) | (1 << 1) | (1 << 2) | (1 << 14))
                    rule = PathRule(rights, path_fd)
                    checked(lib.syscall(445, fd, 1, ctypes.byref(rule), 0))
                finally:
                    os.close(path_fd)
        checked(lib.prctl(38, 1, 0, 0, 0))  # no_new_privs
        checked(lib.syscall(446, fd, 0))
    finally:
        os.close(fd)


def drop_privileges(uid: int):
    if os.geteuid() != 0 or uid < 10000:
        raise RuntimeError("Host must be root and child must use a dedicated unprivileged UID")
    lib = libc()
    # Clear every bounding capability before losing CAP_SETPCAP.
    for capability in range(64):
        result = lib.prctl(24, capability, 0, 0, 0)
        if result < 0 and ctypes.get_errno() != errno.EINVAL:
            checked(result)
    checked(lib.prctl(38, 1, 0, 0, 0))
    os.setgroups([])
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)


class Filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

