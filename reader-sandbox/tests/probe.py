"""Run inside the sandbox by test_sandbox.py: report what this process can and can't do, as JSON."""
import ctypes
import errno
import json
import os
import resource
import socket
import sys


def attempt(fn):
    try:
        fn()
        return "allowed"
    except OSError as e:
        return errno.errorcode.get(e.errno, str(e.errno))


def raw_syscall(number, *args):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(number, *args) == -1:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))


secret, work = sys.argv[1], sys.argv[2]
status = dict(line.split(":\t", 1) for line in open("/proc/self/status").read().splitlines() if ":\t" in line)
print(json.dumps({
    "uid": os.getuid(), "euid": os.geteuid(), "groups": os.getgroups(),
    "no_new_privs": status["NoNewPrivs"].strip(), "seccomp": status["Seccomp"].strip(),
    "core_limit": resource.getrlimit(resource.RLIMIT_CORE)[0],
    "home": os.environ.get("HOME"), "tmpdir": os.environ.get("TMPDIR"), "umask": oct(os.umask(0o077)),
    "inet_socket": attempt(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()),
    "inet6_socket": attempt(lambda: socket.socket(socket.AF_INET6, socket.SOCK_DGRAM).close()),
    "netlink_socket": attempt(lambda: socket.socket(socket.AF_NETLINK, socket.SOCK_RAW).close()),
    "unix_socket": attempt(lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM).close()),
    "ptrace": attempt(lambda: raw_syscall(101, 0, 0, 0, 0)),  # PTRACE_TRACEME (x86_64)
    "unshare": attempt(lambda: os.unshare(os.CLONE_NEWUSER) if hasattr(os, "unshare") else raw_syscall(272, 0x10000000)),
    "io_uring": attempt(lambda: raw_syscall(425, 1, 0)),  # io_uring_setup
    "read_secret": attempt(lambda: open(secret).read()),
    "write_work": attempt(lambda: open(os.path.join(work, "probe.txt"), "w").write("x")),
    "write_root": attempt(lambda: open("/root/probe-should-fail", "w").write("x")),
    "setuid_root": attempt(lambda: os.setuid(0)),
}))
