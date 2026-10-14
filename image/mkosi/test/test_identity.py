"""The identity tool (mkosi.extra/usr/lib/suveryn/identity) on this machine, sealed with the host key
only (no TPM here). Needs root, systemd-creds and openssl; skipped otherwise. The TPM-sealed case is
tested in the virtual machine (run-vm-tests).

    sudo python3 -m pytest image/mkosi/test/test_identity.py
"""

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "mkosi.extra" / "usr" / "lib" / "suveryn" / "identity"

pytestmark = pytest.mark.skipif(os.geteuid() != 0 or not shutil.which("systemd-creds") or not shutil.which("openssl")
                                or Path("/dev/tpmrm0").exists(),
                                reason="needs root, systemd-creds and openssl, and no TPM (host key only)")


def run(d: Path, *args, data: bytes | None = None):
    return subprocess.run([str(TOOL), *args], input=data, capture_output=True,
                          env={**os.environ, "SUVERYN_IDENTITY_DIR": str(d)})


@pytest.fixture(scope="module")
def ident(tmp_path_factory):
    d = tmp_path_factory.mktemp("identity") / "id"
    r = run(d, "create")
    assert r.returncode == 0, r.stderr
    return d


def test_it_is_made_with_four_files(ident):
    assert sorted(p.name for p in ident.iterdir()) == ["identity.json", "private.cred", "public.pem", "uuid"]
    assert stat.S_IMODE((ident / "private.cred").stat().st_mode) == 0o600
    d = json.loads((ident / "identity.json").read_text())
    assert uuid.UUID(d["uuid"]).version == 4 and (ident / "uuid").read_text() == d["uuid"] + "\n"
    assert d["algorithm"] == "ed25519" and d["sealed"] == "host"
    assert (ident / "public.pem").read_text() == d["public_key"]


def test_the_fingerprint_is_the_sha256_of_the_raw_key(ident):
    der = subprocess.run(["openssl", "pkey", "-pubin", "-in", str(ident / "public.pem"), "-outform", "DER"],
                         capture_output=True, check=True).stdout
    assert json.loads((ident / "identity.json").read_text())["fingerprint"] == hashlib.sha256(der[-32:]).hexdigest()


def test_the_private_key_is_never_stored_plain(ident):
    for p in ident.iterdir():
        assert b"PRIVATE KEY" not in p.read_bytes(), p.name
    assert subprocess.run(["openssl", "pkey", "-in", str(ident / "private.cred"), "-noout"],
                          capture_output=True).returncode != 0


def test_a_second_create_changes_nothing(ident):
    before = {p.name: p.read_bytes() for p in ident.iterdir()}
    assert run(ident, "create").returncode == 0
    assert {p.name: p.read_bytes() for p in ident.iterdir()} == before


def test_show(ident):
    r = run(ident, "show", "--json")
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["usable"] is True and out["uuid"] == (ident / "uuid").read_text().strip()


def test_sign_and_verify(ident, tmp_path):
    (tmp_path / "data").write_bytes(b"check-in 2026-10-14")
    sig = run(ident, "sign", str(tmp_path / "data")).stdout
    assert len(base64.b64decode(sig)) == 64
    (tmp_path / "sig").write_bytes(sig)
    assert run(ident, "verify", str(tmp_path / "data"), str(tmp_path / "sig")).returncode == 0
    assert run(ident, "sign", "-", data=b"check-in 2026-10-14").stdout == sig  # Ed25519 is deterministic
    # openssl alone (as the portal would) accepts it
    (tmp_path / "sig.bin").write_bytes(base64.b64decode(sig))
    assert subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(ident / "public.pem"), "-rawin",
                           "-in", str(tmp_path / "data"), "-sigfile", str(tmp_path / "sig.bin")],
                          capture_output=True).returncode == 0
    (tmp_path / "data").write_bytes(b"check-in 2026-10-15")
    r = run(ident, "verify", str(tmp_path / "data"), str(tmp_path / "sig"))
    assert r.returncode == 1 and r.stdout.strip() == b"INVALID"


def test_another_appliances_key_doesnt_verify(ident, tmp_path):
    other = tmp_path / "other"
    assert run(other, "create").returncode == 0
    (tmp_path / "data").write_bytes(b"x")
    (tmp_path / "sig").write_bytes(run(other, "sign", str(tmp_path / "data")).stdout)
    assert run(ident, "verify", str(tmp_path / "data"), str(tmp_path / "sig")).returncode == 1
    assert run(ident, "verify", str(tmp_path / "data"), str(tmp_path / "sig"),
               "--public-key", str(other / "public.pem")).returncode == 0


def test_without_an_identity(tmp_path):
    r = run(tmp_path / "none", "show")
    assert r.returncode == 3 and b"made at first boot" in r.stderr


def test_usage(ident):
    for args in ([], ["show", "x"], ["sign"], ["verify", "a"], ["nonsense"]):
        assert run(ident, *args).returncode == 2
