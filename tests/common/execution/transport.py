"""Fixed-destination TCP emulation for hosts without network namespaces.

Never CONTINUE a notified syscall or dereference an agent sockaddr. Every
Internet stream socket is supplied already connected to the one host gateway.
connect() is emulated as success; other destinations are never contacted.
Not a general network stack: IPv4 HTTP over this transport only is supported.
The gateway must independently authenticate and constrain every request.
"""
from __future__ import annotations

import array
import ctypes
import errno
import os
import select
import socket
import threading

from .linux import Filter, Program, checked, libc


class Data(ctypes.Structure):
    _fields_ = [('nr', ctypes.c_int), ('arch', ctypes.c_uint),
                ('ip', ctypes.c_uint64), ('args', ctypes.c_uint64 * 6)]


class Notification(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint64), ('pid', ctypes.c_uint),
                ('flags', ctypes.c_uint), ('data', Data)]


class Response(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint64), ('val', ctypes.c_int64),
                ('error', ctypes.c_int32), ('flags', ctypes.c_uint)]


class AddFD(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint64), ('flags', ctypes.c_uint),
                ('srcfd', ctypes.c_uint), ('newfd', ctypes.c_uint),
                ('newfd_flags', ctypes.c_uint)]


def ioc(direction, number, structure):
    return (direction << 30) | (ctypes.sizeof(structure) << 16) | (ord('!') << 8) | number


def install_transport(control_fd: int | None):
    """Called in a single-threaded child, after dropping privilege/Landlock.

    No control FD means completely offline. Descendants cannot add seccomp
    filters, use alternate socket interfaces or obtain other processes' FDs.
    """
    deny = 0x00050000 | errno.EPERM
    notify = 0x7FC00000
    instructions = [(0x20,0,0,4), (0x15,1,0,0xC000003E),
                    (0x06,0,0,0x80000000), (0x20,0,0,0),
                    (0x45,0,1,0x40000000), (0x06,0,0,0x80000000)]
    # socket/connect are emulated, never executed in the child.
    for nr in (41, 42):
        instructions.extend([(0x15,0,1,nr), (0x06,0,0,notify if control_fd is not None else deny)])
    # accept/bind/listen/accept4, ptrace, io_uring, pidfd_getfd, seccomp,
    # process_vm_*, bpf, perf, mount/namespace, open_by_handle_at.
    for nr in (43,49,50,288,101,425,426,427,438,317,310,311,321,298,165,166,272,308,304,
               155,161,133,259):
        instructions.extend([(0x15,0,1,nr), (0x06,0,0,deny)])
    # Prevent PR_SET_SECCOMP too. Other prctl calls are needed by runtimes.
    instructions.extend([(0x15,0,3,157), (0x20,0,0,16),
                         (0x15,0,1,22), (0x06,0,0,deny), (0x06,0,0,0x7FFF0000)])
    filters = (Filter * len(instructions))(*(Filter(*x) for x in instructions))
    program = Program(len(instructions), filters)
    lib = libc()
    checked(lib.prctl(38,1,0,0,0))
    fd = checked(lib.syscall(317,1,8 if control_fd is not None else 0,ctypes.byref(program)))
    if control_fd is not None:
        control = socket.socket(fileno=control_fd)  # existing pre-filter socketpair
        control.sendmsg([b'listener'], [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))])
        control.close()
        os.close(fd)


def receive_listener(control: socket.socket) -> int:
    control.settimeout(20)
    _, ancillary, _, _ = control.recvmsg(64,socket.CMSG_SPACE(4))
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            descriptors = array.array('i'); descriptors.frombytes(data[:4])
            return descriptors[0]
    raise RuntimeError('Child did not supply a seccomp listener')


class FixedGatewayBroker:
    def __init__(self, listener: int, port: int, max_sockets=256):
        self.listener, self.port, self.max_sockets = listener, port, max_sockets
        self.stop = threading.Event()
        self.injected = 0
        self.denied = 0
        self.errors = []
        self.thread = threading.Thread(target=self.run,daemon=True)
        self.thread.start()

    def run(self):
        lib = libc()
        while not self.stop.is_set():
            ready, _, _ = select.select([self.listener],[],[],0.2)
            if not ready:
                continue
            note = Notification()
            if lib.ioctl(self.listener,ioc(3,0,Notification),ctypes.byref(note)) < 0:
                if ctypes.get_errno() in (errno.ENOENT,errno.EINTR):
                    continue
                self.errors.append('receive_failed'); return
            response = Response(note.id,0,0,0)  # NEVER SECCOMP_USER_NOTIF_FLAG_CONTINUE
            if note.data.nr == 41:
                family, kind, protocol = note.data.args[:3]
                if (family != socket.AF_INET or kind & 0xF != socket.SOCK_STREAM
                        or kind & ~(socket.SOCK_STREAM | socket.SOCK_CLOEXEC | socket.SOCK_NONBLOCK)
                        or protocol not in (0,socket.IPPROTO_TCP) or self.injected >= self.max_sockets):
                    response.error = -errno.EPERM
                    self.denied += 1
                else:
                    try:
                        with socket.create_connection(('127.0.0.1',self.port),timeout=5) as stream:
                            stream.setblocking(not bool(kind & socket.SOCK_NONBLOCK))
                            addition = AddFD(note.id,2,stream.fileno(),0,
                                             os.O_CLOEXEC if kind & socket.SOCK_CLOEXEC else 0)
                            checked(lib.ioctl(self.listener,ioc(1,3,AddFD),ctypes.byref(addition)))
                        self.injected += 1
                        continue  # ADDFD_FLAG_SEND atomically supplies fd + syscall result
                    except OSError as exc:
                        response.error = -errno.ECONNREFUSED
                        self.errors.append('inject_errno_' + str(exc.errno))
            elif note.data.nr != 42:
                response.error = -errno.EPERM
            # connect returns 0 on a socket already connected exclusively to our
            # gateway. Invalid FDs may get 0 too, but no actual connection occurs.
            lib.ioctl(self.listener,ioc(3,1,Response),ctypes.byref(response))

    def close(self):
        self.stop.set()
        self.thread.join(timeout=6)
        os.close(self.listener)

    def report(self):
        return {'mode':'fixed_gateway_fd_injection','injected':self.injected,
                'denied':self.denied,'errors':self.errors,'continue_used':False,
                'max_sockets':self.max_sockets}
