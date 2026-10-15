#!/usr/bin/env python3
"""The web setup end to end in a real browser: suveryn-core's production setup page, served by nginx
with the appliance's real headers, against this repository's setup service, from Welcome to Complete
and the switch to the real site. On a DISPOSABLE Ubuntu 24.04 machine or container only, as root: it
writes /etc/suveryn, /etc/nginx, /var/lib/suveryn and /opt/suveryn/ui.

    apt-get install nginx; pip install playwright; (chromium for Playwright)
    cd suveryn-core/packages/chat-ui && npm ci && npm run build && cp -r dist /opt/suveryn/ui
    python3 setup/tests/browser_check.py [OUT_DIR]

What stands in for the appliance: suveryn-provision (stand_in_provision.py: same steps, the real TLS and
nginx site), the outgoing-traffic check, the identity (a fixed UUID), the portal's key (a test key made
here), and a share's server (a listener on port 445 at this machine's own address).
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import ssl
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SETUP = HERE.parent
REPO = SETUP.parent
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="setup-check-"))
APPLIANCE = "7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41"
NAME = socket.gethostname().split(".")[0]
HOST = f"{NAME}.local"
PASSWORD = "violet harbour lantern 47"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + str(detail) if detail else ''}", flush=True)


def sh(*cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True, **kw).stdout


def ipv6() -> bool:
    try:
        socket.socket(socket.AF_INET6).close()
        return True
    except OSError:
        return False


def checkout() -> Path:
    """A copy of this repository where an appliance has it, /opt/suveryn/appliance (readable by the
    setup service's user). On a machine without IPv6 (some containers) its nginx sites don't listen
    on [::]: nginx refuses a site it can't fully listen for."""
    copy = Path("/opt/suveryn/appliance")
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns(".git", "mkosi.output", "mkosi.cache", "__pycache__"))
    if not ipv6():
        for t in (copy / "setup" / "nginx-setup-site.conf.template", copy / "reverse-proxy" / "suveryn.conf.template"):
            t.write_text("".join(line for line in t.read_text().splitlines(True) if "listen [::]" not in line))
        print("(no IPv6 here: the nginx sites run without their [::] listen lines)", flush=True)
    return copy


def machine() -> dict:
    """The disposable machine as an appliance would be at its first start."""
    global SETUP, HERE
    if os.geteuid() != 0:
        sys.exit("browser_check.py: as root, on a disposable machine")
    run_from = checkout()
    SETUP, HERE = run_from / "setup", run_from / "setup" / "tests"
    subprocess.run(["nginx", "-s", "quit"], capture_output=True)
    for line in subprocess.run(["ps", "-eo", "pid,comm,args"], capture_output=True, text=True).stdout.splitlines():
        pid, comm, args = (line.split(None, 2) + ["", ""])[:3]
        if comm in ("python3", "runuser") and re.search(r"suveryn-setup (serve|watch)", args):
            os.kill(int(pid), 15)  # an earlier run's
    time.sleep(0.5)
    for path in ("/etc/suveryn", "/var/lib/suveryn/setup", "/etc/nginx/sites-available/suveryn",
                 "/etc/nginx/sites-enabled/suveryn", "/run/suveryn"):
        shutil.rmtree(path, ignore_errors=True) if Path(path).is_dir() and not Path(path).is_symlink() else Path(path).unlink(missing_ok=True)
    Path("/etc/suveryn/tls").mkdir(parents=True)
    ident = Path("/var/lib/suveryn/identity")
    ident.mkdir(parents=True, exist_ok=True)
    (ident / "identity.json").write_text(json.dumps({"uuid": APPLIANCE, "fingerprint": "42a9e1f0" * 8}))
    subprocess.run(["useradd", "--system", "--user-group", "--no-create-home", "--shell", "/usr/sbin/nologin",
                    "suveryn-setup"], capture_output=True)
    keys = run_from / "support" / "portal-keys"   # where the setup service looks by default
    keys.mkdir(parents=True, exist_ok=True)
    sh("openssl", "genpkey", "-algorithm", "ed25519", "-out", OUT / "portal.key")
    sh("openssl", "pkey", "-in", OUT / "portal.key", "-pubout", "-out", keys / "portal.pem")
    tool = run_from / "support" / "suveryn-manifest"
    for name, appliance in (("manifest.yaml", APPLIANCE), ("other.yaml", APPLIANCE.replace("7f3c", "0000"))):
        sh(tool, "make", OUT / name, "--appliance", appliance, "--edition", "Sūveryn Enterprise", "--users", "15",
           "--valid-until", "2027-09-30", "--next-check-in", "2026-12-31", "--connectors", "3", "--playbooks", "4")
        sh(tool, "sign", OUT / name, "--key-file", OUT / "portal.key")
    check_tool = run_from / "network-mode-check"
    check_tool.write_text("#!/bin/sh\necho '{\"passed\": true, \"summary\": \"only approved destinations reachable (stand-in)\"}'\n")
    check_tool.chmod(0o755)
    env = {**os.environ, "SUVERYN_PROVISION": str(HERE / "stand_in_provision.py"),
           "SUVERYN_NETWORK_MODE_TOOL": str(check_tool), "STAND_IN_FAIL_AT": "Keycloak", "SUVERYN_SETUP_SEEN_DONE_WAIT": "60"}
    r = subprocess.run([sys.executable, SETUP / "suveryn-setup", "prepare"], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"prepare failed: {r.stdout[-500:]} {r.stderr[-500:]}")
    serve = subprocess.Popen(["runuser", "-u", "suveryn-setup", "--", sys.executable, "-I", SETUP / "suveryn-setup", "serve"],
                             env=env, stdout=open(OUT / "serve.log", "w"), stderr=subprocess.STDOUT)
    watch = subprocess.Popen([sys.executable, "-u", SETUP / "suveryn-setup", "watch"], env=env,
                             stdout=open(OUT / "watch.log", "w"), stderr=subprocess.STDOUT)
    # A share's server on the office's network: this machine's own address, port 445.
    ip = json.loads(sh("ip", "-j", "-4", "addr", "show", "scope", "global"))[0]["addr_info"][0]["local"]
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((ip, 445))
    listener.listen()
    threading.Thread(target=lambda: [listener.accept()[0].close() for _ in iter(int, 1)], daemon=True).start()
    time.sleep(1)
    return {"serve": serve, "watch": watch, "ip": ip}


def direct():
    """Straight to this machine (no proxy), trusting its setup certificate as the browser was told to."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))


VIOLATIONS = """window.__csp = []; document.addEventListener('securitypolicyviolation',
  e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));"""


def flow(browser, m):
    ctx = browser.new_context(ignore_https_errors=True, accept_downloads=True, locale="en-GB")
    ctx.add_init_script(VIOLATIONS)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"https://{HOST}/")
    pg.get_by_role("heading", name="Set up your sūveryn appliance", level=1).wait_for()
    r = direct().open(urllib.request.Request("https://127.0.0.1/", headers={"Host": HOST}), timeout=10)
    csp = r.headers.get("Content-Security-Policy", "")
    check("the setup page has the strict CSP and the security headers", "style-src 'self'" in csp
          and r.headers.get("X-Frame-Options") == "DENY" and r.headers.get("Strict-Transport-Security"))
    pg.get_by_role("button", name="Get started").click()

    pg.get_by_role("heading", name="Network", level=1).wait_for()
    check("Network shows the address in use", m["ip"] in pg.content(), m["ip"])
    pg.get_by_role("button", name="Continue").click()

    pg.get_by_role("heading", name="Administrator account", level=1).wait_for()
    pg.locator("input[name=name]").fill("Filip Tack")
    pg.locator("input[name=email]").fill("filip@firm.example")
    pg.locator("input[name=password]").fill(PASSWORD)
    pg.locator("input[name=confirm]").fill(PASSWORD)
    pg.get_by_role("button", name="Continue").click()

    pg.get_by_role("heading", name="Certificate", level=1).wait_for()
    check("the administrator's password is on the appliance once, readable by the setup service only",
          oct(os.stat("/var/lib/suveryn/setup/answers/administrator-password").st_mode & 0o777) == "0o600")
    # The office's own certificate, with a key that isn't its own: the appliance refuses it.
    certs = OUT / "certs"
    certs.mkdir(exist_ok=True)
    sh("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "30",
       "-subj", f"/CN={HOST}", "-addext", f"subjectAltName=DNS:{HOST}", "-keyout", certs / "a.key", "-out", certs / "a.crt")
    sh("openssl", "genpkey", "-algorithm", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", certs / "b.key")
    pg.get_by_text("Use your own certificate").click()
    for i, f in enumerate(("a.crt", "b.key")):
        with pg.expect_file_chooser() as fc:
            pg.locator(".ui-file").nth(i).get_by_role("button").click()
        fc.value.set_files(certs / f)
    pg.get_by_role("button", name="Continue").click()
    msg = pg.get_by_text("This key doesn't belong to the certificate.")
    msg.wait_for()
    check("Certificate: the appliance's refusal (a key that isn't the certificate's) shows under the field", msg.is_visible())
    pg.get_by_text("Generate a self-signed certificate").click()
    pg.get_by_role("button", name="Continue").click()

    pg.get_by_role("heading", name="Support agreement", level=1).wait_for()
    check("Support shows the appliance's ID for registration", APPLIANCE in pg.content())
    drop = pg.locator("input[type=file]").first
    drop.set_input_files(OUT / "other.yaml")
    other = pg.get_by_text("This manifest is for another appliance.")
    other.wait_for()
    check("Support: a manifest for another appliance is refused by the appliance", other.is_visible())
    pg.locator("input[type=file]").first.set_input_files(OUT / "manifest.yaml")
    pg.get_by_text("Sūveryn Enterprise").first.wait_for()
    check("Support: the signed manifest for this appliance is accepted and shown", True)
    pg.get_by_role("button", name="Continue").click()

    pg.get_by_role("heading", name="Backups", level=1).wait_for()
    pg.get_by_text("Network share", exact=True).click()
    pg.locator("input[name=address]").fill("\\\\8.8.8.8\\backups")
    pg.get_by_role("button", name="Test connection").click()
    pg.get_by_text("This address isn't on your own network.").wait_for()
    check("Backups: a share outside the office's network is refused", True)
    pg.locator("input[name=address]").fill(f"\\\\{m['ip']}\\backups")
    pg.locator("input[name=user]").fill("backup-suveryn")
    pg.locator("input[name=password]").fill("share secret 1")
    pg.get_by_role("button", name="Test connection").click()
    pg.locator(".setup-test-status[data-state=ok]").wait_for()
    check("Backups: a share on the office's network is reached", True)
    with pg.expect_download() as dl:
        pg.get_by_role("button", name="Download the recovery key").click()
    key = Path(dl.value.path()).read_text()
    pg.locator(".ui-checkbox").click()
    pg.get_by_role("button", name="Continue").click()

    pg.get_by_role("heading", name="Review", level=1).wait_for()
    answers = json.loads(Path("/var/lib/suveryn/setup/answers/answers.json").read_text())
    recipient = re.search(r"# public key: (age1\S+)", key).group(1)
    check("only the recovery key's public half reached the appliance",
          answers["backups"]["recipient"] == recipient and "AGE-SECRET-KEY" not in json.dumps(answers)
          and not any("AGE-SECRET" in p.read_text(errors="ignore") for p in Path("/var/lib/suveryn/setup/answers").iterdir()))
    pg.screenshot(path=OUT / "6-review.png", full_page=True)
    pg.get_by_role("button", name="Start provisioning").click()

    # The stand-in fails once at Keycloak: the screen says so and offers Try again.
    pg.get_by_role("button", name="Try again").wait_for(timeout=30000)
    check("Provisioning: a failed step is shown with Try again",
          pg.locator("[data-state=failed]").count() >= 1)
    pg.screenshot(path=OUT / "7-failed.png", full_page=True)
    pg.get_by_role("button", name="Try again").click()
    pg.get_by_role("heading", name="Setup complete", level=1).wait_for(timeout=60000)
    check("Provisioning runs to Complete through the switch to the real nginx site", True)
    check("Complete shows the sign-in address", f"https://{HOST}" in pg.content())
    pg.screenshot(path=OUT / "8-complete.png", full_page=True)
    check("no CSP violations and no script errors on any screen",
          pg.evaluate("window.__csp.length") == 0 and not errors, f"{pg.evaluate('window.__csp')} {errors}")
    ctx.close()


def after(m):
    for _ in range(30):
        if Path("/etc/suveryn/setup-complete").exists():
            break
        time.sleep(1)
    check("setup closes: setup-complete written, setup locations and answers gone",
          Path("/etc/suveryn/setup-complete").exists() and not Path("/etc/suveryn/nginx-setup.conf").exists()
          and not Path("/var/lib/suveryn/setup/answers").exists() and not Path("/etc/suveryn/tls/setup-cert.pem").exists())
    def get(path):
        try:
            r = direct().open(urllib.request.Request(f"https://127.0.0.1{path}", headers={"Host": HOST}), timeout=10)
            return r.status, r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
    status, body = get("/")
    check("afterwards / is the chat, and the setup page and API are gone",
          status == 200 and "setup" not in re.search(r"<title>(.*?)</title>", body).group(1).lower()
          and get("/setup.html")[0] == 404 and get("/setup/v1/state")[0] == 404, status)
    kept = json.loads(Path("/etc/suveryn/setup-answers.json").read_text())
    check("what was chosen is kept without secrets", kept["support"]["edition"] == "Sūveryn Enterprise"
          and PASSWORD not in json.dumps(kept) and "share secret 1" not in json.dumps(kept))
    check("the share's password is kept for the backup service, root only",
          oct(os.stat("/etc/suveryn/backups-share.secret").st_mode & 0o777) == "0o600")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    m = machine()
    try:
        browse(m)
        after(m)
    finally:
        m["serve"].terminate()
        m["watch"].terminate()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed; screenshots and logs in {OUT}")
    return 1 if failed else 0


def browse(m):
    with sync_playwright() as p:
        # The browser reaches the appliance at 127.0.0.1 (some test machines send everything else through a proxy).
        browser = p.chromium.launch(args=[f"--host-resolver-rules=MAP {HOST} 127.0.0.1", "--no-proxy-server"],
                                    env={k: v for k, v in os.environ.items() if "proxy" not in k.lower()})
        try:
            flow(browser, m)
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
