"""suveryn-provision's decisions that need no machine: the GPU licence check, environment and unit
files, llama-server presets, and the test PDF. The installation itself is checked by
``suveryn-provision verify`` on the machine (provision/README.md). Any machine:

    python3 -m pytest provision/tests
"""

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader("provision", str(HERE / "suveryn-provision"))
spec = importlib.util.spec_from_loader("provision", loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)


@pytest.mark.parametrize(("site", "may", "says"), [
    ("on-premise", True, "on-premise office installation"),
    ("test", True, "TEST or development machine"),
    ("datacenter", False, "forbids datacenter deployment"),
    (None, False, "Say where this machine runs"),
])
def test_a_geforce_card_needs_the_site_said_and_never_runs_in_a_datacenter(site, may, says):
    ok, message = p.gpu_verdict(["NVIDIA GeForce RTX 4090"], site)
    assert ok is may and says in message


def test_a_datacenter_card_has_no_site_restriction_and_no_gpu_stops():
    assert p.gpu_verdict(["NVIDIA RTX 6000 Ada Generation"], None)[0] is True
    assert p.gpu_verdict(["NVIDIA L40S"], "datacenter")[0] is True
    assert p.gpu_verdict([], "on-premise") == (False, "no NVIDIA GPU found (nvidia-smi); llama-server and Docling need one")


def test_environment_files_round_trip_and_refuse_injection(tmp_path, monkeypatch):
    monkeypatch.setattr(p.pwd, "getpwnam", lambda name: type("pw", (), {"pw_uid": -1, "pw_gid": -1}))
    monkeypatch.setattr(p.os, "chown", lambda *a: None)
    path = tmp_path / "gateway.env"
    p.write_env(path, {"SUVERYN_DATABASE_URL": "postgresql://suveryn:pw@127.0.0.1:5432/suveryn"}, "suveryn", header="# x\n")
    assert p.read_env(path) == {"SUVERYN_DATABASE_URL": "postgresql://suveryn:pw@127.0.0.1:5432/suveryn"}
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SystemExit):
        p.write_env(path, {"A": "x\nSUVERYN_EXTRACT_UNSANDBOXED=1"}, "suveryn")  # a value can't add a line
    assert "SUVERYN_EXTRACT_UNSANDBOXED" not in path.read_text()


def test_the_test_runner_reads_the_same_units_systemd_does():
    unit = p.parse_unit(HERE / "systemd" / "suveryn-gateway.service")
    assert unit["User"] == "suveryn"
    assert unit["env_files"] == [(False, Path("/etc/suveryn/gateway.env")), (True, Path("/etc/suveryn/network-mode.env"))]
    assert unit["ExecStart"] == "/opt/suveryn/core/.venv/bin/suveryn-gateway"
    for name in p.UNITS:
        u = p.parse_unit(HERE / "systemd" / f"{name}.service")
        assert u.get("User") and u.get("ExecStart")


def test_presets_come_from_the_manifest():
    llms = [{"id": "qwen3.8-27b", "file": "Q.gguf", "default": True,
             "llama_server_args": "-ngl 99 --jinja --chat-template-kwargs '{\"enable_thinking\":false}'"},
            {"id": "mistral-small-3.2-24b", "file": "M.gguf", "llama_server_args": "-ngl 99"}]
    ini = p.presets(llms)
    qwen, mistral = ini.split("[qwen3.8-27b]")[1].split("[mistral-small-3.2-24b]")
    assert f"model = {p.MODELS / 'Q.gguf'}" in qwen and "jinja = true" in qwen and "load-on-startup = true" in qwen
    assert 'chat-template-kwargs = {"enable_thinking":false}' in qwen
    assert "jinja" not in mistral and "load-on-startup" not in mistral


def test_the_test_pdf_is_a_valid_one_page_pdf(tmp_path):
    pdf = p.test_pdf()
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    xref = int(pdf.rsplit(b"startxref\n", 1)[1].split(b"\n")[0])
    assert pdf[xref:xref + 4] == b"xref"
    offsets = [int(line[:10]) for line in pdf[xref:].split(b"\n")[3:8]]
    for i, offset in enumerate(offsets, 1):
        assert pdf[offset:].startswith(b"%d 0 obj" % i)
    pdfium = pytest.importorskip("pypdfium2")  # where available, read it back
    path = tmp_path / "t.pdf"
    path.write_bytes(pdf)
    doc = pdfium.PdfDocument(str(path))
    assert len(doc) == 1 and "Koopprijs" in doc[0].get_textpage().get_text_range()


def test_pins_are_complete():
    v = json.loads((HERE / "versions.json").read_text())
    assert len(v["keycloak"]["sha256"]) == 64 and len(v["uv"]["sha256"]) == 64
    assert len(v["llama_cpp"]["commit"]) == 40 and len(v["suveryn_core"]["commit"]) == 40


def test_it_refuses_to_run_as_another_user():
    if sys.platform != "linux" or __import__("os").geteuid() == 0:
        pytest.skip("checks the refusal as a normal user")
    r = subprocess.run([sys.executable, str(HERE / "suveryn-provision"), "verify"], capture_output=True, text=True, check=False)
    assert r.returncode == 1 and "run as root" in r.stderr


def test_the_reader_gets_its_variables_through_env_keep_not_preserve_env():
    """sudo refuses --preserve-env without SETENV (found on the test pod: the reader didn't start), and
    SETENV would let the gateway pass the reader any variable. env_keep names exactly what it needs."""
    assert "--preserve-env" not in p.READER_WRAPPER and "SETENV" not in p.READER_WRAPPER
    needed = {"HF_HOME", "SUVERYN_EXTRACT_WORK_DIR", "SUVERYN_OCR_JOBS"}  # suveryn-core's child_env keeps these
    assert needed <= set(p.READER_ENV.split())


def test_a_failure_shows_the_lines_that_name_what_failed():
    """apt's last 600 characters left out which package's set-up failed (found in an image build)."""
    out = "\n".join(["Setting up foo (1.0) ..."] * 200 + ["dpkg: error processing package postgresql-16 (--configure):",
                     " installed post-installation script subprocess returned error exit status 1",
                     "Errors were encountered while processing:", " postgresql-16"] + ["noise"] * 50)
    shown = p.error_lines(out)
    assert "dpkg: error processing package postgresql-16" in shown and "Errors were encountered" in shown
    assert p.error_lines("all fine, nothing to see") == "all fine, nothing to see"


def test_the_sandbox_boundary_check_flags_every_way_out():
    held = {"uid": 997, "euid": 997, "inet_socket": "EPERM", "inet6_socket": "EPERM", "netlink_socket": "EPERM",
            "read_secret": "EACCES", "ptrace": "EPERM", "setuid_root": "EPERM", "write_root": "EACCES",
            "unshare": "EPERM", "no_new_privs": "1", "seccomp": "2", "core_limit": 0}
    assert p.sandbox_boundary(held, 997) == []
    leaks = {**held, "uid": 0, "inet_socket": "allowed", "read_secret": "allowed", "seccomp": "0"}
    found = " ".join(p.sandbox_boundary(leaks, 997))
    assert "uid 0" in found and "inet_socket" in found and "secrets" in found and "seccomp 0" in found
