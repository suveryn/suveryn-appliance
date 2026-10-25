"""The web setup's appliance side (setup/suveryn-setup): the checks behind each screen, the API as the
screens call it, and provisioning with its hand-over, with stand-ins for suveryn-provision and the
outgoing-traffic check. Any machine with openssl:

    python3 -m pytest setup/tests

The screens against this service in a real browser, through nginx: setup/README.md, "Testing".
"""

import base64
import datetime
import importlib.machinery
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent
CORE = Path(os.environ.get("SUVERYN_CORE", REPO.parent / "suveryn-core"))
APPLIANCE = "7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41"
# A throwaway age recipient (age-keygen, as in suveryn-core's screens.test.ts). Not used anywhere.
RECIPIENT = "age166srcke2l538za388de4cly32dhk96rg8yp6ckng0962fderf3yq64whup"
GOOD_PASSWORD = "lantern orchard ribbon 42"


def load(tmp: Path, **env):
    """A fresh copy of the tool whose folders are all under tmp."""
    defaults = {"SUVERYN_SETUP_TEST": "1", "SUVERYN_SETUP_DIR": tmp / "setup", "SUVERYN_ETC": tmp / "etc",
                "SUVERYN_VAR": tmp / "var", "SUVERYN_NGINX": tmp / "nginx", "SUVERYN_LOGS": tmp / "log",
                "SUVERYN_SETUP_WORK": tmp / "run", "SUVERYN_SETUP_SEEN_DONE_WAIT": "5",
                "SUVERYN_SETUP_SHARE_TIMEOUT": "0.5", "SUVERYN_PORTAL_KEYS": tmp / "portal-keys"}
    for k, v in {**defaults, **env}.items():
        os.environ[k] = str(v)
    loader = importlib.machinery.SourceFileLoader("suveryn_setup", str(HERE / "suveryn-setup"))
    spec = importlib.util.spec_from_loader("suveryn_setup", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    for d in (mod.STATE, mod.ANSWERS, mod.ETC / "tls", tmp / "portal-keys"):
        d.mkdir(parents=True, exist_ok=True)
    return mod


@pytest.fixture
def s(tmp_path, monkeypatch):
    mod = load(tmp_path)
    facts = {"name": "notary-brussels-01", "language": "nl", "appliance": {"id": APPLIANCE, "fingerprint": "ab" * 32},
             "models": ["Qwen3.8-27B"]}
    (mod.STATE / "facts.json").write_text(json.dumps(facts))
    monkeypatch.setattr(mod, "network", lambda: {"address": "192.168.1.50/24", "dhcp": True, "gateway": "192.168.1.1",
                                                  "dns": "192.168.1.1"})
    monkeypatch.setattr(mod, "usb_disks", lambda: [{"id": "usb-SanDisk_Extreme_4C53", "name": "SanDisk Extreme",
                                                     "size_bytes": 1_000_204_886_016, "bus": "USB"}])
    yield mod
    for k in list(os.environ):
        if k.startswith("SUVERYN_"):
            del os.environ[k]


# ---------------------------------------------------------------------------- the administrator
def test_the_password_rules_are_the_screens():
    mod = load(Path("/tmp"))
    ok = mod.check_administrator({"name": " Anne  de Smet ", "email": "anne@office.be ", "password": GOOD_PASSWORD}, "notary-01")
    assert ok == {"name": "Anne de Smet", "email": "anne@office.be", "password": GOOD_PASSWORD}
    for answer, codes in [
        ({"name": "", "email": "anne", "password": "short"}, {"name": "name_missing", "email": "email_invalid",
                                                              "password": "password_short"}),
        ({"name": "Anne", "email": "a@b.be", "password": "adminadminadmin"}, {"password": "password_common"}),
        ({"name": "Anne", "email": "a@b.be", "password": "aaaaaaaaaaaaaaaa"}, {"password": "password_common"}),
        ({"name": "Anne", "email": "a@b.be", "password": "abcdefghijklmnop"}, {"password": "password_common"}),
        ({"name": "Anne Vermeulen", "email": "a@b.be", "password": "my vermeulen secret 9"}, {"password": "password_personal"}),
        ({"name": "Anne", "email": "jansens@b.be", "password": "Jansens and more words"}, {"password": "password_personal"}),
        ({"name": "Anne", "email": "a@b.be", "password": "the notary office secret"}, {"password": "password_personal"}),
    ]:
        with pytest.raises(mod.Refused) as e:
            mod.check_administrator(answer, "notary-01")
        assert e.value.codes == codes, answer


def test_the_common_password_list_is_the_screens(tmp_path):
    ts = CORE / "packages" / "chat-ui" / "src" / "setup" / "administrator.ts"
    if not ts.exists():
        pytest.skip(f"no suveryn-core checkout at {CORE} (set SUVERYN_CORE)")
    listed = set(re.findall(r'"([^"]+)"', re.search(r"const COMMON = new Set\(\[(.*?)\]\)", ts.read_text(), re.S).group(1)))
    assert listed == load(tmp_path).COMMON_PASSWORDS
    assert f"MIN_PASSWORD = {load(tmp_path).MIN_PASSWORD}" in ts.read_text()


# ---------------------------------------------------------------------------- the certificate
def openssl(*args, cwd):
    subprocess.run(["openssl", *map(str, args)], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def certs(tmp_path):
    """An office CA, and certificates from it: right, for another name, expired; a stranger's key."""
    d = tmp_path / "certs"
    d.mkdir()
    openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30",
            "-subj", "/CN=Office CA", "-keyout", "ca.key", "-out", "ca.crt", cwd=d)
    for name, san, days in (("good", "DNS:notary-brussels-01.local", 30), ("other", "DNS:intranet.office.be", 30),
                            ("wild", "DNS:*.local", 30), ("old", "DNS:notary-brussels-01.local", None)):
        openssl("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", f"/CN={name}",
                "-keyout", f"{name}.key", "-out", f"{name}.csr", cwd=d)
        (d / f"{name}.ext").write_text(f"subjectAltName={san}\n")
        if days:
            openssl("x509", "-req", "-in", f"{name}.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
                    "-days", days, "-extfile", f"{name}.ext", "-out", f"{name}.crt", cwd=d)
        else:  # openssl 3.0's x509 can't back-date; its ca command can
            (d / "index.txt").write_text("")
            (d / "ca.cnf").write_text("[ca]\ndefault_ca=x\n[x]\ndatabase=index.txt\nnew_certs_dir=.\nserial=serial\n"
                                      "default_md=sha256\npolicy=p\ncopy_extensions=none\n[p]\ncommonName=supplied\n")
            (d / "serial").write_text("1000\n")
            openssl("ca", "-batch", "-config", "ca.cnf", "-cert", "ca.crt", "-keyfile", "ca.key", "-in", f"{name}.csr",
                    "-startdate", "20200101000000Z", "-enddate", "20200102000000Z", "-extfile", f"{name}.ext",
                    "-out", f"{name}.crt", "-notext", cwd=d)
    openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30",
            "-subj", "/CN=Elsewhere CA", "-keyout", "x.key", "-out", "x.crt", cwd=d)
    openssl("pkey", "-in", "good.key", "-out", "locked.key", "-aes256", "-passout", "pass:secret", cwd=d)
    return {p.name: p.read_text() for p in d.iterdir() if p.is_file()}


def test_the_offices_certificate_is_checked_before_it_is_saved(s, certs):
    host = "notary-brussels-01.local"
    answer, files = s.check_certificate({"kind": "own", "certificate": certs["good.crt"], "key": certs["good.key"],
                                         "chain": certs["ca.crt"]}, host)
    assert answer["kind"] == "own" and answer["names"] == [host] and answer["issuer"] == "Office CA"
    assert answer["not_after"] == (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    assert files["certificate.pem"].count(b"BEGIN CERTIFICATE") == 2 and b"PRIVATE KEY" in files["key.pem"]
    # A wildcard for .local covers the name; the chain may also come after the certificate in one file.
    assert s.check_certificate({"kind": "own", "certificate": certs["wild.crt"] + certs["ca.crt"], "key": certs["wild.key"]},
                               host)[0]["names"] == ["*.local"]
    for answer, codes in [
        ({"certificate": "nonsense", "key": certs["good.key"]}, {"certificate": "not_a_certificate"}),
        ({"certificate": certs["good.crt"], "key": "nonsense"}, {"key": "not_a_key"}),
        ({"certificate": certs["good.crt"], "key": certs["locked.key"]}, {"key": "not_a_key"}),
        ({"certificate": certs["good.crt"], "key": certs["x.key"]}, {"key": "key_mismatch"}),
        ({"certificate": certs["old.crt"], "key": certs["old.key"]}, {"certificate": "expired"}),
        ({"certificate": certs["other.crt"], "key": certs["other.key"]}, {"certificate": "name_not_covered"}),
        ({"certificate": certs["good.crt"], "key": certs["good.key"], "chain": certs["x.crt"]}, {"chain": "chain_invalid"}),
        ({"certificate": certs["good.crt"], "key": certs["good.key"], "chain": "x" * 70000}, {"chain": "not_a_certificate"}),
    ]:
        with pytest.raises(s.Refused) as e:
            s.check_certificate({"kind": "own", **answer}, host)
        assert e.value.codes == codes, codes


def test_the_appliances_own_certificate_names_the_name_and_the_address(s):
    assert s.check_certificate({"kind": "self-signed"}, "notary-brussels-01.local") == (
        {"kind": "self-signed", "names": ["notary-brussels-01.local", "192.168.1.50"]}, {})


# ---------------------------------------------------------------------------- support and backups
def test_a_manifest_is_taken_only_when_signed_for_this_appliance(s, tmp_path):
    key = tmp_path / "portal.key"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", key], check=True)
    subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-out", tmp_path / "portal-keys" / "portal.pem"], check=True)
    m = s.manifests
    release = m.load("release", REPO / "bundles" / "suveryn-bundle-release")
    signed = m.sign(m.make(APPLIANCE, "Sūveryn Enterprise", 15, "2027-09-30", "2026-12-31", 3, 4), release.KeyFile(key))
    facts = s.App().facts()
    assert s.check_manifest(signed, facts) == {"edition": "Sūveryn Enterprise", "appliance_id": APPLIANCE, "users": 15,
                                                "valid_until": "2027-09-30", "next_check_in": "2026-12-31",
                                                "connectors": 3, "playbooks": 4}
    other = m.sign(m.make(APPLIANCE.replace("7f3c", "0000"), "Sūveryn Enterprise", 15, "2027-09-30", "2026-12-31"),
                   release.KeyFile(key))
    expired = m.sign(m.make(APPLIANCE, "Sūveryn Enterprise", 15, "2020-09-30", "2020-12-31"), release.KeyFile(key))
    for raw, code in ((signed.replace(b"users: 15", b"users: 50"), "manifest_signature"),
                      (signed.split(b"signature:")[0], "manifest_signature"), (other, "manifest_other_appliance"),
                      (expired, "manifest_expired"), (b"edition: [a, b]\n", "manifest_format"),
                      (signed.replace(b"users: 15", b"users: '15'"), "manifest_format"), (b"\xff\xfe", "manifest_format")):
        with pytest.raises(s.Refused) as e:
            s.check_manifest(raw, facts)
        assert e.value.codes == {"_": code}, raw[:60]
    with pytest.raises(s.Refused) as e:
        s.check_manifest(signed, {"appliance": None})
    assert e.value.codes == {"_": "manifest_other_appliance"}


def test_a_manifest_is_valid_yaml(s):
    yaml = pytest.importorskip("yaml")
    raw = s.manifests.make(APPLIANCE, "Sūveryn Enterprise", 15, "2027-09-30", "2026-12-31", 3, 4)
    raw += b"signature: " + b"ab" * 32 + b" " + base64.b64encode(b"x" * 64) + b"\n"
    parsed = yaml.safe_load(raw)
    assert parsed["users"] == 15 and parsed["edition"] == "Sūveryn Enterprise" and parsed["appliance_id"] == APPLIANCE


def test_backups_need_a_real_recovery_key_and_a_disk_or_share_that_exists(s):
    usb = {"target": "usb", "disk": "usb-SanDisk_Extreme_4C53", "retention_days": 30, "recipient": RECIPIENT}
    assert s.check_backups(usb) == (usb, None)
    share = {"target": "share", "protocol": "smb", "address": "\\\\nas01\\backups", "user": "backup", "password": "pw",
             "retention_days": 90, "recipient": RECIPIENT}
    assert s.check_backups(share) == ({"target": "share", "protocol": "smb", "address": "\\\\nas01\\backups", "user": "backup",
                                       "retention_days": 90, "recipient": RECIPIENT}, "pw")
    assert s.check_backups({"target": "later"}) == ({"target": "later"}, None)
    assert s.check_backups({**share, "protocol": "nfs", "address": "nas01:/export/backups", "user": None, "password": None})[1] is None
    broken = RECIPIENT[:-1] + ("q" if RECIPIENT[-1] != "q" else "p")
    for answer, codes in [({**usb, "disk": "usb-Gone_1"}, {"disk": "disk_gone"}),
                          ({**usb, "recipient": broken}, {"_": "invalid"}),
                          ({**usb, "recipient": "age1" + "q" * 10}, {"_": "invalid"}),
                          ({**usb, "retention_days": 7}, {"_": "invalid"}),
                          ({**share, "address": "nas01/backups"}, {"address": "address"})]:
        with pytest.raises(s.Refused) as e:
            s.check_backups(answer)
        assert e.value.codes == codes, answer


def test_the_share_test_stays_on_the_offices_network(s):
    assert s.test_share({"protocol": "smb", "address": "backups"}) == {"ok": False, "error": "address"}
    assert s.test_share({"protocol": "smb", "address": "\\\\8.8.8.8\\share"}) == {"ok": False, "error": "not_local"}
    assert s.test_share({"protocol": "nfs", "address": "127.0.0.1:/x"}) == {"ok": False, "error": "not_local"}
    assert s.test_share({"protocol": "nfs", "address": "10.255.255.1:/export"}) == {"ok": False, "error": "unreachable"}


# ---------------------------------------------------------------------------- files
def test_files_are_written_through_a_rename_and_never_read_through_a_link(s, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("root's secret")
    link = s.ANSWERS / "administrator-password"
    link.symlink_to(secret)
    with pytest.raises(OSError):
        s.read_file(link)
    s.write_file(link, "new")
    assert secret.read_text() == "root's secret" and not link.is_symlink() and link.read_text() == "new"
    assert stat.S_IMODE(link.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------- the API
class Client:
    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}/setup/v1/"

    def __call__(self, method, path, body=None, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Host": "notary-brussels-01.local", **({"Content-Type": ctype} if data is not None else {}), **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"null")
            except ValueError:
                return e.code, None


@pytest.fixture
def api(s):
    from http.server import ThreadingHTTPServer
    s.Handler.app = s.App()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), s.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield Client(httpd.server_address[1])
    httpd.shutdown()


def answer_everything(api):
    assert api("PUT", "answers/network", {"mode": "connected", "outgoing": "approved"}) == (204, None)
    assert api("PUT", "answers/administrator", {"name": "Anne de Smet", "email": "anne@office.be",
                                                "password": GOOD_PASSWORD}) == (204, None)
    assert api("PUT", "answers/certificate", {"kind": "self-signed"})[0] == 200
    assert api("PUT", "answers/support", {"skipped": True}) == (204, None)
    assert api("PUT", "answers/backups", {"target": "usb", "disk": "usb-SanDisk_Extreme_4C53", "retention_days": 30,
                                          "recipient": RECIPIENT}) == (204, None)


def test_the_screens_contract_is_answered(s, api):
    ts = CORE / "packages" / "chat-ui" / "src" / "setup" / "api.ts"
    if not ts.exists():
        pytest.skip(f"no suveryn-core checkout at {CORE} (set SUVERYN_CORE)")
    routes = re.findall(r"^ \*   (GET|PUT|POST)\s+setup/v1/(\S+)", ts.read_text(), re.M)
    assert len(routes) == 13
    for method, path in routes:
        assert f"{method} {path}" in s.WRITES or (method, path) in (("GET", "state"), ("GET", "provision"), ("GET", "restore")), path


def test_state_and_answers_survive_a_reload_and_secrets_never_come_back(s, api):
    status, state = api("GET", "state")
    assert status == 200 and state["name"] == "notary-brussels-01" and state["language"] == "nl"
    assert state["appliance"]["id"] == APPLIANCE and state["usb_disks"][0]["bus"] == "USB"
    assert state["answers"] == {} and state["provisioning"] is None
    answer_everything(api)
    _, state = api("GET", "state")
    assert state["answers"]["administrator"] == {"name": "Anne de Smet", "email": "anne@office.be"}
    assert GOOD_PASSWORD not in json.dumps(state)
    assert (s.ANSWERS / "administrator-password").read_text() == GOOD_PASSWORD
    assert stat.S_IMODE((s.ANSWERS / "administrator-password").stat().st_mode) == 0o600


def test_refusals_come_as_codes_by_field(api):
    assert api("PUT", "answers/administrator", {"name": "", "email": "x", "password": "y"}) == (
        422, {"errors": {"name": "name_missing", "email": "email_invalid", "password": "password_short"}})
    assert api("POST", "manifest", b"not a manifest", "application/octet-stream") == (422, {"error": "manifest_format"})
    assert api("POST", "provision", {}) == (422, {"error": "not_answered"})


def test_only_the_appliances_own_page_can_call_it(api):
    assert api("PUT", "answers/network", {"mode": "connected", "outgoing": "all"},
               headers={"Origin": "https://evil.example"}) == (403, {"error": "cross_origin"})
    assert api("PUT", "answers/network", {"mode": "connected", "outgoing": "all"},
               headers={"Sec-Fetch-Site": "cross-site"}) == (403, {"error": "cross_origin"})
    assert api("PUT", "answers/network", b'{"mode":"connected","outgoing":"all"}', "text/plain") == (415, {"error": "json_only"})
    assert api("PUT", "answers/network", {"mode": "connected", "outgoing": "all"},
               headers={"Origin": "https://notary-brussels-01.local", "Sec-Fetch-Site": "same-origin"}) == (204, None)
    assert api("GET", "anything")[0] == 404 and api("DELETE", "state")[0] in (404, 501)
    assert api("PUT", "answers/network", b"x" * 400_000)[0] == 413


def test_provisioning_is_asked_for_once_and_answers_then_stay_as_they_are(s, api):
    answer_everything(api)
    status, p = api("POST", "provision")  # as the screen sends it: no body
    assert status == 200 and p["state"] == "running" and all(st["state"] == "waiting" for st in p["steps"])
    assert (s.ANSWERS / "start").exists()
    assert api("GET", "provision")[1]["state"] == "running"
    assert api("PUT", "answers/network", {"mode": "air-gapped", "outgoing": "approved"}) == (422, {"error": "provisioning_started"})
    assert api("POST", "provision", {})[1]["state"] == "running"


# ---------------------------------------------------------------------------- provisioning
FAKE_PROVISION = """#!/usr/bin/env python3
import json, sys, time
args = sys.argv[1:]
open(sys.argv[0] + ".args", "w").write(json.dumps(args))
admin = json.load(open(args[args.index("--administrator") + 1]))
assert admin["password"] == "%s", admin
for title in ["GPU licence check", "Network mode", "PostgreSQL 16 + pgvector", "Keycloak: host name, secrets and the production realm",
              "Model files (suveryn-core models/manifest.json)", "TLS certificate", "Verify"]:
    print("\\n== " + title, flush=True)
    print("   working", flush=True)
    if "%s" and title.startswith("%s"):
        print("suveryn-provision: Keycloak didn't start", flush=True)
        sys.exit(1)
"""


@pytest.fixture
def fake_tools(s, tmp_path, monkeypatch):
    def make(fail_at=""):
        tool = tmp_path / "fake-provision"
        tool.write_text(FAKE_PROVISION % (GOOD_PASSWORD, fail_at, fail_at))
        tool.chmod(0o755)
        check = tmp_path / "fake-network-mode"
        check.write_text("#!/bin/sh\necho '{\"passed\": true, \"summary\": \"only approved destinations reachable\"}'\n")
        check.chmod(0o755)
        monkeypatch.setattr(s, "PROVISION", tool)
        monkeypatch.setattr(s, "NETWORK_MODE_TOOL", check)
        monkeypatch.setattr(s, "models_media", lambda work: None)
        return tool
    return make


def test_provisioning_reports_each_step_then_closes_setup_once_complete_was_shown(s, api, fake_tools):
    tool = fake_tools()
    answer_everything(api)
    api("POST", "provision", {})
    s.NGINX_SETUP.write_text("# setup locations")
    s.SETUP_CERT.write_text("cert")
    seen = []

    def browser():  # the screen polls, and sees Complete
        for _ in range(100):
            p = api("GET", "provision")[1]
            seen.append(p and p["state"])
            if p and p["state"] == "done":
                return
            threading.Event().wait(0.05)
    t = threading.Thread(target=browser)
    t.start()
    assert s.provision() == 0
    t.join()
    args = json.loads(Path(f"{tool}.args").read_text())
    assert args[:3] == ["first-boot", "--public-url", "https://notary-brussels-01.local"]
    assert args[args.index("--network-mode") + 1] == "connected" and args[args.index("--outbound") + 1] == "approved"
    assert args[args.index("--site") + 1] == "on-premise"
    assert not (s.ANSWERS.exists())                        # the password file with it
    assert not Path(args[args.index("--administrator") + 1]).exists()
    assert s.SETUP_COMPLETE.exists() and not s.NGINX_SETUP.exists() and not s.SETUP_CERT.exists()
    done = json.loads((s.STATE / "progress.json").read_text())
    assert done == {"state": "done", "outgoing": "limited", "steps": [{"id": i, "state": "done"} for i in s.STEPS]}
    kept = json.loads((s.ETC / "setup-answers.json").read_text())
    assert kept["administrator"] == {"name": "Anne de Smet", "email": "anne@office.be"} and GOOD_PASSWORD not in json.dumps(kept)
    assert json.loads((s.ETC / "backups.json").read_text())["recipient"] == RECIPIENT
    assert "done" in seen


def test_a_failed_step_is_reported_and_can_be_tried_again(s, api, fake_tools):
    fake_tools(fail_at="Keycloak")
    answer_everything(api)
    api("POST", "provision", {})
    assert s.provision() == 1
    p = api("GET", "provision")[1]
    assert p["state"] == "failed"
    assert [(st["id"], st["state"]) for st in p["steps"]] == [("database", "done"), ("signin", "failed"),
                                                              ("models", "waiting"), ("web", "waiting"), ("outgoing", "waiting")]
    assert p["steps"][1]["error"] == "suveryn-provision: Keycloak didn't start"
    assert not s.SETUP_COMPLETE.exists() and (s.ANSWERS / "administrator-password").exists()
    # Try again: running at once (the old failure doesn't flash back), then the runner takes over.
    assert api("POST", "provision", {})[1]["state"] == "running"
    assert api("GET", "provision")[1]["state"] == "running"


def test_answers_tampered_with_in_the_services_folder_stop_provisioning(s, api, fake_tools):
    fake_tools()
    answer_everything(api)
    api("POST", "provision", {})
    (s.ANSWERS / "administrator-password").write_text("short")
    assert s.provision() == 1
    assert json.loads((s.STATE / "progress.json").read_text())["steps"][0] == {"id": "database", "state": "failed",
                                                                               "error": "answers"}


def test_the_outgoing_result_follows_the_mode(s, api, fake_tools, monkeypatch):
    fake_tools()
    for network, passed, outgoing in ((("air-gapped", "approved"), True, "blocked"), (("connected", "all"), True, "not_checked"),
                                      (("connected", "approved"), False, "not_limited")):
        answer_everything(api)
        api("PUT", "answers/network", {"mode": network[0], "outgoing": network[1]})
        check = Path(s.NETWORK_MODE_TOOL)
        check.write_text(f"#!/bin/sh\necho '{{\"passed\": {str(passed).lower()}}}'\n")
        (s.ANSWERS / "seen-done").touch()
        api("POST", "provision", {})
        assert s.provision() == 0
        assert json.loads((s.STATE / "progress.json").read_text())["outgoing"] == outgoing
        s.SETUP_COMPLETE.unlink()
        (s.STATE / "progress.json").unlink()
        s.ANSWERS.mkdir()


# ---------------------------------------------------------------------------- nginx
def test_the_setup_locations_keep_the_headers_and_stay_on_the_offices_network():
    snippet = (HERE / "nginx-setup.conf").read_text()
    blocks = dict(re.findall(r"location\s+([^{]+)\{(.*?)\n\}", snippet, re.S))
    assert set(b.strip() for b in blocks) == {"= /", "/setup/v1/"}
    for body in blocks.values():
        assert "include /etc/nginx/snippets/suveryn-headers.conf;" in body
        assert "include /etc/nginx/snippets/suveryn-csp.conf;" in body
        assert body.strip().count("deny all;") == 1 and "allow 192.168.0.0/16;" in body and "allow 0.0.0.0" not in body
    assert "proxy_pass http://127.0.0.1:@PORT@;" in blocks["/setup/v1/ "]
    site = (HERE / "nginx-setup-site.conf.template").read_text()
    assert site.startswith("# sūveryn web setup site") and "include /etc/suveryn/nginx-setup*.conf;" in site
    real = (REPO / "reverse-proxy" / "suveryn.conf.template").read_text()
    assert "include /etc/suveryn/nginx-setup*.conf;" in real and re.search(r"location = /setup.html \{\s*return 404;", real)
    assert re.search(r"location /setup/ \{[^}]*\n\s*return 404;", real)


def test_prepare_writes_the_facts_and_the_setup_site(tmp_path, monkeypatch):
    mod = load(tmp_path)
    (mod.VAR / "host").mkdir(parents=True)
    (mod.VAR / "host" / "hostname").write_text("notary-ghent\n")
    (mod.VAR / "identity").mkdir()
    (mod.VAR / "identity" / "identity.json").write_text(json.dumps({"uuid": APPLIANCE, "fingerprint": "cd" * 32}))
    (mod.VAR / "installed.json").write_text(json.dumps({"language": "fr", "models": [{"name": "qwen3-30b-a3b"}]}))
    monkeypatch.setattr(mod, "network", lambda: {"address": "10.0.0.7/24", "dhcp": False, "gateway": "", "dns": ""})
    calls = []
    real = mod.provision_module()  # its nginx helpers, with the TLS step standing in

    def tls(host, cert, key, ips):
        calls.append((host, cert, key, ips))
        (mod.ETC / "tls" / "server.crt").write_text("the local CA's certificate")
    monkeypatch.setattr(real, "tls", tls)
    monkeypatch.setattr(mod, "provision_module", lambda: real)
    mod.prepare()
    assert json.loads((mod.STATE / "facts.json").read_text()) == {
        "name": "notary-ghent", "language": "fr", "appliance": {"id": APPLIANCE, "fingerprint": "cd" * 32},
        "models": ["qwen3-30b-a3b"]}
    assert calls == [("notary-ghent.local", None, None, ["10.0.0.7"])]
    assert mod.SETUP_CERT.read_text() == "the local CA's certificate"
    site = (mod.NGINX / "sites-available" / "suveryn").read_text()
    assert "server_name notary-ghent.local _;" in site
    assert (mod.NGINX / "sites-enabled" / "suveryn").resolve() == (mod.NGINX / "sites-available" / "suveryn")
    assert "proxy_pass http://127.0.0.1:8090;" in mod.NGINX_SETUP.read_text()
    # The real site, once provisioning wrote it, is left alone.
    (mod.NGINX / "sites-available" / "suveryn").write_text("# the real site")
    mod.prepare()
    assert (mod.NGINX / "sites-available" / "suveryn").read_text() == "# the real site"


def test_on_the_read_only_system_the_site_goes_to_the_data_partition(tmp_path, monkeypatch):
    """image/mkosi: /etc/nginx/sites-enabled/suveryn is a link into /var/lib/suveryn/nginx, built in."""
    mod = load(tmp_path)
    real = mod.provision_module()
    data = tmp_path / "var-nginx" / "suveryn"
    enabled = mod.NGINX / "sites-enabled" / "suveryn"
    enabled.parent.mkdir(parents=True)
    monkeypatch.setattr(real.os, "readlink", lambda p: "/var/lib/suveryn/nginx/suveryn" if Path(p) == enabled else os.readlink(p))
    enabled.symlink_to(data)
    real.NGINX = mod.NGINX
    monkeypatch.setattr(real, "Path", lambda p: data if p == "/var/lib/suveryn/nginx/suveryn" else Path(p))
    assert real.site_file() == data and data.parent.is_dir()


def test_serve_refuses_to_run_as_root(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("needs root to see the refusal")
    r = subprocess.run([sys.executable, HERE / "suveryn-setup", "serve"], capture_output=True, text=True,
                       env={"PATH": os.environ["PATH"], "SUVERYN_SETUP_DIR": str(tmp_path)})
    assert r.returncode != 0 and "not as root" in r.stderr


# ---------------------------------------------------------------------------- restoring a backup (#56)
# A throwaway age identity (age-keygen), in the file setup's browser makes. Not used anywhere.
KEY = "AGE-SECRET-KEY-1QYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSZQGPQYQSUHVQ6Q"
KEY_FILE = f"# created: 2026-10-24T09:00:00Z\n# public key: {RECIPIENT}\n{KEY}\n"


def test_where_the_backups_are_and_the_key_are_checked(s):
    disks = [{"id": "usb-SanDisk_Extreme_4C53"}]
    answer, key, password = s.check_restore({"target": "usb", "disk": "usb-SanDisk_Extreme_4C53", "key": KEY_FILE}, disks)
    assert answer == {"target": "usb", "disk": "usb-SanDisk_Extreme_4C53"} and key == KEY and password is None
    answer, key, password = s.check_restore({"target": "share", "protocol": "smb", "address": "\\\\nas\\backups",
                                             "user": "bk", "password": "pw", "key": KEY}, disks)
    assert answer["protocol"] == "smb" and "key" not in answer and password == "pw"
    for bad, code in (({"target": "usb", "disk": "usb-SanDisk_Extreme_4C53", "key": "AGE-SECRET-KEY-1short"}, {"key": "key_invalid"}),
                      ({"target": "usb", "disk": "usb-Gone", "key": KEY}, {"disk": "disk_gone"}),
                      ({"target": "share", "protocol": "smb", "address": "nas", "key": KEY}, {"address": "address"}),
                      ({"target": "later", "key": KEY}, {"_": "invalid"})):
        with pytest.raises(s.Refused) as e:
            s.check_restore(bad, disks)
        assert e.value.codes == code


def stand_in(path: Path, text: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + text)
    path.chmod(0o755)
    return path


def test_a_backup_is_found_checked_restored_and_setup_closes(s, api, tmp_path):
    seen = tmp_path / "seen.json"
    s.BACKUP_TOOL = stand_in(tmp_path / "backup-tool", f"""import json, sys
args = sys.argv[1:]
settings = json.load(open(args[args.index("--settings") + 1]))
key = open(args[args.index("--identity") + 1]).read().strip()
json.dump({{"settings": settings, "key_ok": key == {KEY!r}}}, open({str(seen)!r}, "w"))
print(json.dumps({{"usable": True, "file": "suveryn-backup_notary-ghent_20261023T023900Z.tar.zst.age",
                  "created": "2026-10-23T02:39:00Z", "appliance": "notary-ghent", "entries": {{}}, "files": ["etc"]}}))
""")
    s.PROVISION = stand_in(tmp_path / "provision", """import sys
assert sys.argv[1] == "restore" and "--backup" in sys.argv
for t in ("PostgreSQL 16 + pgvector", "Restoring the backup", "Model files (x)", "Network mode", "Starting the services", "Verify"):
    print("== " + t, flush=True)
""")
    status, body = api("PUT", "answers/restore", {"target": "usb", "disk": "usb-SanDisk_Extreme_4C53", "key": KEY_FILE})
    assert status == 202 and body == {"state": "checking"}
    assert stat.S_IMODE((s.ANSWERS / "recovery-key").stat().st_mode) == 0o600
    _, state = api("GET", "state")
    assert state["restore"] == {"state": "checking"} and KEY not in json.dumps(state)
    assert s.restore_check() == 0
    assert json.loads(seen.read_text()) == {"settings": {"target": "usb", "disk": "usb-SanDisk_Extreme_4C53"}, "key_ok": True}
    _, r = api("GET", "restore")
    assert r == {"state": "checked", "backup": {"file": "suveryn-backup_notary-ghent_20261023T023900Z.tar.zst.age",
                                                "created": "2026-10-23T02:39:00Z", "appliance": "notary-ghent"}}
    assert api("PUT", "answers/network", {"mode": "connected", "outgoing": "approved"}) == (204, None)  # still open until it starts
    status, r = api("POST", "restore/start")
    assert status == 200 and r["state"] == "restoring"
    assert api("PUT", "answers/network", {"mode": "connected", "outgoing": "approved"}) == (422, {"error": "restore_started"})
    assert s.restore_run() == 0
    done = json.loads(s.RESTORE.read_text())
    assert done["state"] == "done" and done["address"] == "https://notary-ghent.local"
    assert [st["state"] for st in done["steps"]] == ["done"] * 5
    assert s.SETUP_COMPLETE.exists() and not s.ANSWERS.exists()  # the key and the answers gone with setup


def test_a_restore_check_that_fails_says_why_and_the_restore_cant_start(s, api, tmp_path):
    for step, message, code in (("decrypt", "age: no identity matched", "wrong_key"), ("target", "there is no backup on the target", "no_backup"),
                                ("target", "the backup disk isn't connected", "unreachable"), ("read", "damaged", "damaged"),
                                ("version", "the backup was made with PostgreSQL 18, newer than this appliance's 16", "newer")):
        s.BACKUP_TOOL = stand_in(tmp_path / f"tool-{code}", f"import json; print(json.dumps({{'usable': False, 'step': {step!r}, 'message': {message!r}}})); raise SystemExit(1)\n")
        assert api("PUT", "answers/restore", {"target": "usb", "disk": "usb-SanDisk_Extreme_4C53", "key": KEY})[0] == 202
        assert s.restore_check() == 1
        assert api("GET", "restore")[1] == {"state": "failed", "error": code}
        assert not (s.ANSWERS / "recovery-key").exists()  # asked for again, not kept
    assert api("POST", "restore/start") == (422, {"error": "not_checked"})
