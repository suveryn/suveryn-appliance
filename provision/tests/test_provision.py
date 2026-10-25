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


def test_first_boot_settings_for_cases_are_checked(tmp_path):
    env = tmp_path / "first-boot.env"
    env.write_text("PUBLIC_URL=https://suveryn.office.example\nCASES_ACCESS=office\nCASES_KEEP=1y\n")
    assert p.first_boot_config(env) == {"PUBLIC_URL": "https://suveryn.office.example", "CASES_ACCESS": "office", "CASES_KEEP": "1y"}
    for bad in ("CASES_ACCESS=everyone", "CASES_KEEP=2y", "CASES_PERIOD=1y", "CONVERSATIONS_KEEP=forever"):
        env.write_text(f"PUBLIC_URL=https://suveryn.office.example\n{bad}\n")
        with pytest.raises(SystemExit):
            p.first_boot_config(env)


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


def test_a_server_started_without_systemd_never_holds_a_pipe(tmp_path):
    """#37 6b on the Pod: nginx, started directly, kept the error output pipe it was given open, and
    whoever started it waited for that pipe to close, forever. A stand-in that does the same: it
    leaves a child in the background holding everything it was given."""
    import time
    daemon = tmp_path / "fake-daemon"
    daemon.write_text("#!/bin/sh\necho starting >&2\n(sleep 30 &)\nexit 0\n")
    daemon.chmod(0o755)
    start = time.time()
    p.start_daemon([str(daemon)], "the stand-in")
    assert time.time() - start < 10
    failing = tmp_path / "failing-daemon"
    failing.write_text("#!/bin/sh\necho 'bind() to 0.0.0.0:443 failed' >&2\nexit 1\n")
    failing.chmod(0o755)
    with pytest.raises(SystemExit):
        p.start_daemon([str(failing)], "the stand-in")


def test_the_read_check_deletes_its_test_pdf_by_scope(tmp_path):
    """READ_CHECK runs inside suveryn-core's virtualenv; here against a stand-in for suveryn_rag with
    core's signatures (accept_upload and job by owner, deleting by Scope). Deleting with a plain owner
    string, as before core's cases, fails here as it did on the pod. The space is emptied before the
    upload too: an earlier run's leftover is the same file, which core would return instead of reading."""
    pkg = tmp_path / "suveryn_rag"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "config.py").write_text("class RagSettings:\n    @classmethod\n    def from_env(cls): return cls()\n")
    (pkg / "store.py").write_text(
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\nclass Scope:\n    owner: str | None = None\n    case_id: object = None\n"
        "    @classmethod\n    def personal(cls, owner): return cls(owner=owner)\n"
        "    def where(self): return ('owner = %s', (self.owner,))\n")
    (pkg / "service.py").write_text(
        "import types\nfrom suveryn_rag.store import Scope\n"
        "DELETED = []\n"
        "class DocumentService:\n"
        "    state, error = 'ready', None\n"
        "    def __init__(self, settings): pass\n"
        "    def start(self): pass\n"
        "    def accept_upload(self, src, filename, owner): assert isinstance(owner, str); src.read(); return types.SimpleNamespace(id='j1')\n"
        "    def job(self, job_id, owner): return types.SimpleNamespace(status='ready', pages=1, error=None, warnings=[], document_id='d1')\n"
        "    def delete(self, document_id, scope): scope.where(); DELETED.append(scope)\n"
        "    def delete_all(self, scope):\n"
        "        scope.where()\n"
        "        assert scope == Scope.personal('provisioning-check')\n"
        "        print('DELETED_ALL', flush=True); return 1\n")
    r = subprocess.run([sys.executable, "-c", p.READ_CHECK], input=p.test_pdf(), capture_output=True,
                       env={"PYTHONPATH": str(tmp_path)}, timeout=60)
    out = r.stdout.decode()
    assert r.returncode == 0, r.stderr.decode()[-500:]
    assert out.count("DELETED_ALL") == 2  # a clean space before the upload, and after
    assert json.loads(out.strip().splitlines()[-1]) == {"state": "ready", "status": "ready", "pages": 1, "error": None, "warnings": []}


def test_restarting_under_systemd_restarts_the_running_units(monkeypatch):
    """After an update the running gateway must come back on the new code: ``systemctl start`` leaves a
    running unit as it is, ``restart`` doesn't (and starts a stopped one, as on a first boot)."""
    calls = []
    monkeypatch.setattr(p, "systemd_running", lambda: True)
    monkeypatch.setattr(p, "run", lambda *cmd, **kw: calls.append(list(cmd)) or "")
    p.services("restart")
    assert ["systemctl", "restart", *p.UNITS] in calls
    assert ["systemctl", "start", "postgresql"] in calls and ["systemctl", "start", "nginx"] in calls


def test_restarting_with_the_test_runner_stops_then_starts(monkeypatch):
    """Without systemd (a test pod) a restart is a stop and a start: the start alone skipped a service that
    still ran, which is how an updated pod kept the old gateway."""
    real, done = p.services, []
    monkeypatch.setattr(p, "systemd_running", lambda: False)
    monkeypatch.setattr(p, "services", lambda action: done.append(action) if action != "restart" else real(action))
    real("restart")
    assert done == ["stop", "start"]


def test_first_boot_restarts_the_services():
    """first-boot is what install runs again after an update; it must not merely start what isn't running."""
    import inspect
    source = inspect.getsource(p.first_boot)
    assert 'services("restart")' in source and 'services("start")' not in source


# ---------------------------------------------------------------------------- the read-only system (#47)
def test_on_the_read_only_system_the_trust_bundle_holds_the_systems_cas_and_the_local_one(tmp_path, monkeypatch):
    system = tmp_path / "ca-certificates.crt"
    system.write_bytes(b"SYSTEM CAS\n")
    tls = tmp_path / "tls"
    tls.mkdir()
    (tls / "ca.crt").write_bytes(b"LOCAL CA\n")
    monkeypatch.setattr(p, "read_only_system", lambda: True)
    monkeypatch.setattr(p, "SYSTEM_CA_BUNDLE", system)
    monkeypatch.setattr(p, "TLS", tls)
    monkeypatch.setattr(p, "TRUST_BUNDLE", tls / "ca-bundle.crt")
    monkeypatch.setattr(p, "run", lambda *a, **k: pytest.fail(f"nothing is run on the read-only system: {a}"))
    p.trust_local_ca()
    assert (tls / "ca-bundle.crt").read_bytes() == b"SYSTEM CAS\n\nLOCAL CA\n"
    assert p.ca_bundle() == tls / "ca-bundle.crt"
    monkeypatch.setattr(p, "read_only_system", lambda: False)
    assert p.ca_bundle() == system  # an ordinary Ubuntu: its own bundle, which update-ca-certificates fills


def test_on_the_read_only_system_the_firewall_and_updates_are_the_images(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(p, "read_only_system", lambda: True)
    monkeypatch.setattr(p, "run", lambda *a, **k: ran.append(a))
    p.updates("connected")
    assert ran == [] and "sūveryn updates" in capsys.readouterr().out
    monkeypatch.setattr(p, "UPSTREAM_RESOLV", Path("/nonexistent"))
    with pytest.raises(SystemExit):
        p.firewall()  # stops at the resolver check, after the inbound step
    assert not any(a and a[0] == "ufw" for a in ran)


@pytest.mark.parametrize("release, pg", [("24.04", "16"), ("26.04", "18")])
def test_postgresqls_cluster_is_made_only_when_there_is_none(monkeypatch, release, pg):
    ran = []
    monkeypatch.setattr(p, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(p, "systemd_running", lambda: False)
    monkeypatch.setattr(p, "ubuntu_release", lambda root=None: release)

    class Listed:
        def __init__(self, out):
            self.stdout = out
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: Listed(f"{pg} main 5432 online postgres /var/lib/postgresql/{pg}/main\n"))
    p.ensure_cluster()
    assert ran == []
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: Listed(""))
    p.ensure_cluster()
    assert ran == [("pg_createcluster", pg, "main")]


def test_each_supported_ubuntu_gets_its_own_postgresql_and_others_are_refused(tmp_path):
    """#57: 26.04's archive has PostgreSQL 18, 24.04's has 16; both with pgvector."""
    (tmp_path / "etc").mkdir()
    for release, pg in (("24.04", "16"), ("26.04", "18")):
        (tmp_path / "etc/os-release").write_text(f'NAME="Ubuntu"\nVERSION_ID="{release}"\n')
        assert p.ubuntu_release(tmp_path) == release and p.postgres_major(tmp_path) == pg
        packages = p.for_postgres(p.PACKAGES, pg)
        assert f"postgresql-{pg}" in packages and f"postgresql-{pg}-pgvector" in packages
        assert not any("{" in name for name in packages)
    (tmp_path / "etc/os-release").write_text('VERSION_ID="22.04"\n')
    with pytest.raises(SystemExit):
        p.postgres_major(tmp_path)
    (tmp_path / "etc/os-release").unlink()
    assert p.ubuntu_release(tmp_path) == ""


def test_keycloak_runs_on_java_21_wherever_it_starts():
    """26.04 also has newer Java; Keycloak is started on 21 by its unit and by provision's own runs (#57)."""
    unit = (HERE / "systemd" / "keycloak.service").read_text()
    assert f"Environment=JAVA_HOME={p.JAVA_HOME}" in unit
    assert "java-21-" in str(p.JAVA_HOME) and "openjdk-21-jre-headless" in p.RUNTIME_PACKAGES
    src = (HERE / "suveryn-provision").read_text()
    for call in ('"build", user="keycloak"', '"import", "--file"'):
        line = src[src.index(call):src.index("capture=True", src.index(call))]
        assert '"JAVA_HOME"' in line, call


def test_the_readers_sudo_rule_has_no_requiretty():
    """26.04's sudo-rs refuses `!requiretty` (#57); Ubuntu's sudo never asked for a terminal anyway."""
    assert "requiretty" not in p.reader_sudoers()


def prebuilt_tree(root: Path) -> None:
    o = root / "opt"
    for f, text in (("suveryn/core/.suveryn-built", p.core_built_as()), ("suveryn/llama.cpp/.suveryn-built", p.llama_built_as()),
                    ("keycloak/.suveryn-built", p.keycloak_built_as()), ("suveryn/ui/.suveryn-built", p.ui_built_as()),
                    ("suveryn/core/.venv/bin/suveryn-gateway", ""), ("suveryn/core/.venv/bin/python", ""),
                    ("suveryn/llama.cpp/build/bin/llama-server", ""), ("keycloak/bin/kc.sh", ""), ("suveryn/ui/index.html", ""),
                    ("suveryn/lib/cuda/libcudart.so.13", ""), ("suveryn/hf/hub/models--x/config.json", "")):
        (o / f).parent.mkdir(parents=True, exist_ok=True)
        (o / f).write_text(text)


def test_the_prebuilt_services_must_be_the_pinned_ones_and_complete(tmp_path):
    prebuilt_tree(tmp_path)
    assert p.prebuilt_problems(tmp_path) == []
    (tmp_path / "opt/suveryn/core/.suveryn-built").write_text("suveryn-core@0000000\n")
    (tmp_path / "opt/suveryn/lib/cuda/libcudart.so.13").unlink()
    (tmp_path / "opt/suveryn/core/.git").mkdir()
    problems = p.prebuilt_problems(tmp_path)
    assert len(problems) == 3
    assert "suveryn-core@0000000, not suveryn-core@" in problems[0]
    assert "no CUDA runtime" in problems[1] and "no git history" in problems[2]


def test_on_the_read_only_system_the_models_and_units_are_the_images(monkeypatch, tmp_path):
    monkeypatch.setattr(p, "read_only_system", lambda: True)
    assert p.hf_home() == Path("/opt/suveryn/hf")
    monkeypatch.setattr(p, "read_only_system", lambda: False)
    assert p.hf_home() == Path("/var/lib/suveryn/hf")


def test_keycloaks_host_name_comes_from_its_environment_not_its_configuration_file():
    src = (HERE / "suveryn-provision").read_text()
    first_boot_part = src[src.index("def keycloak(public_url"):src.index("def reader_sudoers")]
    assert '"KC_HOSTNAME": f"https://{host}/idp"' in first_boot_part
    assert "keycloak.conf\").write_text" not in first_boot_part and "conf\" / \"keycloak.conf\").write" not in first_boot_part


def test_the_language_models_come_from_an_installed_bundle_where_it_is(tmp_path, monkeypatch, capsys):
    """The read-only system's one path (#47): the installer's bundle, used where it is, never copied."""
    data = b"weights"
    import hashlib
    m = {"id": "x", "file": "x.gguf", "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(p, "BUNDLES", tmp_path / "bundles")
    assert p.bundled(m) is None
    f = tmp_path / "bundles" / "model" / "x" / "1" / "x.gguf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"changed")
    assert p.bundled(m) is None  # the wrong size or hash: not this model
    f.write_bytes(data)
    assert p.bundled(m) == f
    assert f"model = {f}" in p.presets([{**m, "path": str(f), "default": True}])


def test_on_the_read_only_system_an_air_gapped_first_boot_without_a_bundle_says_where_the_models_come_from(tmp_path, monkeypatch):
    core = tmp_path / "core"
    (core / "models").mkdir(parents=True)
    (core / "models" / "manifest.json").write_text(json.dumps({"models": [
        {"id": "x", "role": "llm", "file": "x.gguf", "size_bytes": 1, "sha256": "0" * 64, "source": "https://example"}]}))
    monkeypatch.setattr(p, "CORE", core)
    monkeypatch.setattr(p, "BUNDLES", tmp_path / "bundles")
    monkeypatch.setattr(p, "read_only_system", lambda: True)
    monkeypatch.setattr(p, "download", lambda *a, **k: pytest.fail("no download when air-gapped"))
    with pytest.raises(SystemExit):
        p.llm_models(None, "air-gapped", staging=False)


def test_the_read_only_images_python_is_uvs_own_and_the_build_host_has_java():
    """Pod check of #47: uv took the build host's Python (the venv pointed at /usr/local/bin), and
    Keycloak's build found no java."""
    src = (HERE / "suveryn-provision").read_text()
    core = src[src.index("def suveryn_core("):src.index("def cuda_libraries(")]
    assert '"UV_PYTHON_PREFERENCE": "only-managed"' in core
    assert "openjdk-21-jre-headless" in p.BUILD_HOST_PACKAGES
    main = src[src.index('if a.command == "prebuild":'):]
    assert main.index("build_host_packages()") < main.index("keycloak_software()")


def test_llama_server_needing_a_library_the_image_lacks_stops_the_prebuild(tmp_path, monkeypatch):
    """VM check of #47: built with NCCL, llama-server couldn't start in the image (libnccl.so.2 not found)."""
    (tmp_path / "build" / "bin").mkdir(parents=True)
    monkeypatch.setattr(p, "LLAMA", tmp_path)
    ldd = ("\tlibcudart.so.12 => /usr/local/cuda/lib64/libcudart.so.12 (0x1)\n"
           "\tlibcuda.so.1 => not found\n\tlibnccl.so.2 => not found\n")
    monkeypatch.setattr(p, "run", lambda *a, **k: ldd)
    with pytest.raises(SystemExit):
        p.cuda_libraries(tmp_path / "out")
    copied = []
    monkeypatch.setattr(p, "run", lambda *a, **k: ldd.replace("\tlibnccl.so.2 => not found\n", "") if a[0] == "ldd" else "")
    monkeypatch.setattr(p.shutil, "copy2", lambda src, dst: copied.append(Path(dst).name))
    assert p.cuda_libraries(tmp_path / "out") == ["libcudart.so.12"]  # the driver's libcuda.so.1 is the image's own
    assert copied == ["libcudart.so.12"]


def test_on_systemd_the_site_is_reloaded_and_the_backup_timer_runs_at_once(monkeypatch, tmp_path):
    """VM check of #33: nginx kept Ubuntu's default site, the timer waited for a reboot, and the backup
    service couldn't read a checkout in /root (ProtectHome)."""
    ran = []
    monkeypatch.setattr(p, "systemd_running", lambda: True)
    monkeypatch.setattr(p, "run", lambda *a, **k: ran.append(a))
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    p.services("restart")
    assert ("systemctl", "reload", "nginx") in ran
    ran.clear()
    monkeypatch.setattr(p, "read_only_system", lambda: False)
    monkeypatch.setattr(p, "REPO", Path("/root/suveryn-appliance"))
    written = {}
    monkeypatch.setattr(p.Path, "write_text", lambda self, text: written.__setitem__(str(self), text))
    monkeypatch.setattr(p.Path, "read_text", lambda self: (HERE.parent / "backup" / "systemd" / self.name).read_bytes().decode())
    p.backup_service()
    unit = written["/etc/systemd/system/suveryn-backup.service"]
    assert "/root/suveryn-appliance/backup/suveryn-backup run" in unit and "ProtectHome=read-only" in unit
    assert ("systemctl", "enable", "--now", "suveryn-backup.timer") in ran


def test_the_temporary_keycloak_administrator_goes_once_the_office_has_its_own(monkeypatch, tmp_path):
    """#55: suveryn-setup (master realm) deleted after setup, and its password out of keycloak.env."""
    deleted, written = [], {}
    monkeypatch.setattr(p, "ETC", tmp_path)
    (tmp_path / "keycloak.env").write_text("KC_HOSTNAME=https://x/idp\nKC_DB_PASSWORD=db\n"
                                            "KC_BOOTSTRAP_ADMIN_USERNAME=suveryn-setup\nKC_BOOTSTRAP_ADMIN_PASSWORD=pw\n")
    monkeypatch.setattr(p, "directory_auth", lambda gw: {"Authorization": "Bearer d"})
    monkeypatch.setattr(p, "keycloak_admin", lambda: {"Authorization": "Bearer a"})
    office_admins = []

    def http(url, headers=None, data=None, timeout=10):
        if "roles/suveryn-admin/users" in url:
            return 200, json.dumps(office_admins).encode()
        if "realms/master/users?username=suveryn-setup" in url:
            return 200, json.dumps([{"id": "u1"}]).encode()
        return 404, b""
    monkeypatch.setattr(p, "http", http)
    monkeypatch.setattr(p.urllib.request, "urlopen", lambda req, timeout=10: deleted.append((req.get_method(), req.full_url)))
    monkeypatch.setattr(p, "write_env", lambda path, values, owner, mode=0o600, header="": written.update(values))
    p.retire_bootstrap_admin({})
    assert deleted == [] and written == {}  # no administrator of the office's own yet: kept
    office_admins.append({"id": "alice"})
    p.retire_bootstrap_admin({})
    assert deleted == [("DELETE", "http://127.0.0.1:8180/idp/admin/realms/master/users/u1")]
    assert written == {"KC_HOSTNAME": "https://x/idp", "KC_DB_PASSWORD": "db"}


def test_without_the_temporary_administrator_verify_says_the_sign_in_check_was_not_run():
    src = (HERE / "suveryn-provision").read_text()
    part = src[src.index("def verify_proxy("):src.index("def verify_sign_in(")]
    assert "if admin_password:" in part and "not checked: the temporary administrator was removed" in part
    first_boot = src[src.index("def first_boot("):src.index("def first_boot_config(")]
    assert first_boot.index("verify(a.public_url)") < first_boot.index("retire_bootstrap_admin(") < first_boot.index("FIRST_BOOT_DONE.write_text")
    assert "if ok and not staging:" in first_boot  # a test machine keeps it


def test_the_images_llama_server_carries_machine_code_for_every_office_card():
    """CUDA 13.2 with driver 580: a card without machine code would need the driver to compile CUDA 13.2's
    intermediate code, which 580 can't (#57). So every generation an office card can be is listed."""
    assert p.IMAGE_CUDA_ARCHITECTURES.split(";") == ["80", "86", "89", "90", "120"]
    src = (HERE / "suveryn-provision").read_text()
    assert '"native" if on_target else IMAGE_CUDA_ARCHITECTURES' in src
