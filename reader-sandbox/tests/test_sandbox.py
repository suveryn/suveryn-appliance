"""suveryn-reader-sandbox, tested from inside the sandbox (probe.py). suveryn-tracker#15.

Needs Linux, root (to create the test users and to start the wrapper as root, as the gateway does
on the dev pod) and python3-seccomp. Elsewhere the tests are skipped. Run:

    sudo python3 -m pytest reader-sandbox/tests
"""

import grp
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
WRAPPER = HERE.parent / "suveryn-reader-sandbox"
PROBE = HERE / "probe.py"
USER = "suveryn-reader"

pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.geteuid() != 0 or shutil.which("useradd") is None,
                                reason="needs Linux and root")


@pytest.fixture(scope="module")
def reader():
    subprocess.run(["/usr/bin/python3", "-c", "import seccomp"], check=True)  # python3-seccomp, for the system Python
    try:
        pwd.getpwnam(USER)
    except KeyError:
        subprocess.run(["useradd", "--system", "--user-group", "--no-create-home", "--shell", "/usr/sbin/nologin", USER],
                       check=True)
    return pwd.getpwnam(USER)


@pytest.fixture
def base():
    """A folder the reader user can enter (pytest's own tmp_path sits under a 0700 folder)."""
    path = Path(tempfile.mkdtemp(prefix="sandbox-test-", dir="/var/tmp"))
    path.chmod(0o755)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def dirs(base, reader):
    tmp_path = base
    work = tmp_path / "reader-work"
    work.mkdir(mode=0o700)
    os.chown(work, reader.pw_uid, reader.pw_gid)
    secret = tmp_path / "gateway.env"
    secret.write_text("SUVERYN_OIDC_CLIENT_SECRET=not-a-real-secret\n")
    secret.chmod(0o600)  # like the gateway's secrets file
    shutil.copy(PROBE, tmp_path / "probe.py")
    return tmp_path, work, secret


def sandboxed(cmd, work, user=None, env=None):
    env = {"PATH": "/usr/bin:/bin", "SUVERYN_EXTRACT_WORK_DIR": str(work), **(env or {})}
    return subprocess.run(["/usr/bin/python3", str(WRAPPER), *cmd], env=env, capture_output=True, text=True,
                          user=user, timeout=60)


def test_the_reader_runs_as_its_own_user_without_network_or_secrets(dirs, reader):
    tmp, work, secret = dirs
    r = sandboxed(["/usr/bin/python3", str(tmp / "probe.py"), str(secret), str(work)], work)
    assert r.returncode == 0, r.stderr
    p = json.loads(r.stdout)
    assert (p["uid"], p["euid"]) == (reader.pw_uid, reader.pw_uid) and p["groups"] in ([], [reader.pw_gid])
    assert p["no_new_privs"] == "1" and p["seccomp"] == "2" and p["core_limit"] == 0
    assert p["home"] == p["tmpdir"] == str(work) and p["umask"] == "0o77"
    assert p["inet_socket"] == p["inet6_socket"] == p["netlink_socket"] == "EACCES"  # no network
    assert p["unix_socket"] == "allowed"
    assert p["ptrace"] == p["unshare"] == p["io_uring"] == "EPERM"
    assert p["read_secret"] == "EACCES" and p["write_root"] == "EACCES" and p["setuid_root"] == "EPERM"
    assert p["write_work"] == "allowed" and stat.S_IMODE((work / "probe.txt").stat().st_mode) == 0o600


def test_child_processes_are_sandboxed_too(dirs):
    """Ghostscript and Tesseract are started by the reader: the filter and the user carry over."""
    tmp, work, secret = dirs
    inner = f"import subprocess,sys; sys.stdout.write(subprocess.run(['/usr/bin/python3','{tmp / 'probe.py'}','{secret}','{work}'],capture_output=True,text=True).stdout)"
    p = json.loads(sandboxed(["/usr/bin/python3", "-c", inner], work).stdout)
    assert p["inet_socket"] == "EACCES" and p["read_secret"] == "EACCES" and p["seccomp"] == "2"


def test_it_refuses_rather_than_run_unsandboxed(dirs, reader):
    tmp, work, _ = dirs
    other = "nobody"
    r = sandboxed(["/usr/bin/true"], work, user=other)  # e.g. the gateway's own user, without sudo
    assert r.returncode == 126 and "refusing to run the reader unsandboxed" in r.stderr
    r = sandboxed(["/usr/bin/true"], work, env={"SUVERYN_READER_USER": "no-such-user"})
    assert r.returncode == 126 and "no user" in r.stderr
    r = sandboxed(["/usr/bin/true"], work, env={"SUVERYN_READER_USER": "root"})
    assert r.returncode == 126 and "must not be root" in r.stderr
    r = subprocess.run(["/usr/bin/python3", str(WRAPPER), "/usr/bin/true"], env={"PATH": "/usr/bin"}, capture_output=True, text=True)
    assert r.returncode == 126 and "SUVERYN_EXTRACT_WORK_DIR" in r.stderr


def test_started_as_the_reader_user_it_stays_that_user(dirs, reader):
    """The appliance gateway isn't root: it starts the wrapper with sudo -u suveryn-reader."""
    tmp, work, secret = dirs
    r = sandboxed(["/usr/bin/python3", str(tmp / "probe.py"), str(secret), str(work)], work, user=USER)
    p = json.loads(r.stdout)
    assert p["uid"] == reader.pw_uid and p["seccomp"] == "2" and p["inet_socket"] == "EACCES"


def test_provisioning_creates_the_folders_with_the_right_owners(base, reader):
    inbox, work = base / "inbox", base / "work"
    script = HERE.parent / "provision-reader.sh"
    for _ in range(2):  # idempotent
        subprocess.run(["sh", str(script), "root", str(inbox), str(work)], check=True, capture_output=True)
    i, w = inbox.stat(), work.stat()
    assert stat.S_IMODE(i.st_mode) == 0o2750 and grp.getgrgid(i.st_gid).gr_name == USER
    assert stat.S_IMODE(w.st_mode) == 0o700 and pwd.getpwuid(w.st_uid).pw_name == USER
