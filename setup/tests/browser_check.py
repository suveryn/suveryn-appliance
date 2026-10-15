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

    python3 setup/tests/browser_check.py --real [OUT_DIR]

The same with the REAL provisioning (#37 part 6b): on a FRESH test GPU machine (the Pod) after
`suveryn-provision image`, never one with real documents or an earlier installation (it refuses
one). suveryn-provision first-boot runs as a test machine (--site test), and afterwards the
administrator from the screens signs in to the chat through Keycloak and `suveryn-provision verify`
runs. The administrator's password is made here and never printed. Provisioning takes as long as
the model download.
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
REAL = "--real" in sys.argv[1:]
_args = [a for a in sys.argv[1:] if a != "--real"]
OUT = Path(_args[0] if _args else tempfile.mkdtemp(prefix="setup-check-"))
APPLIANCE = "7f3c9a2e-41b8-4d0e-9a6c-2e5d18b07f41"
NAME = socket.gethostname().split(".")[0]
HOST = f"{NAME}.local"
import secrets  # noqa: E402
PASSWORD = f"lantern {secrets.token_hex(8)} harbour" if REAL else "violet harbour lantern 47"
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


def sites_enabled() -> None:
    """A test machine whose own nginx.conf doesn't load sites-enabled/ (RunPod's): add it, as the
    provisioning README says. Ubuntu's own nginx.conf, and the appliance's, have it already."""
    conf = Path("/etc/nginx/nginx.conf")
    text = conf.read_text()
    if re.search(r"^\s*include\s+/etc/nginx/sites-enabled/\*;", text, re.M):
        return
    m = re.search(r"^\s*http\s*\{", text, re.M)
    if not m:
        sys.exit("browser_check.py: /etc/nginx/nginx.conf has no http block to add sites-enabled to")
    conf.write_text(text[:m.end()] + "\n    include /etc/nginx/sites-enabled/*;  # added by browser_check.py (sūveryn)\n"
                    + text[m.end():])
    print("(nginx.conf didn't load sites-enabled/: added, as on Ubuntu)", flush=True)


def machine() -> dict:
    """The disposable machine as an appliance would be at its first start."""
    global SETUP, HERE
    if os.geteuid() != 0:
        sys.exit("browser_check.py: as root, on a disposable machine")
    if REAL:  # before anything is written
        for done in ("/etc/suveryn/gateway-oidc.env", "/etc/suveryn/setup-complete", "/var/lib/suveryn/.first-boot-done"):
            if Path(done).exists():
                sys.exit(f"browser_check.py --real: {done} exists: this machine was provisioned before. Use a fresh one.")
    run_from = checkout()
    SETUP, HERE = run_from / "setup", run_from / "setup" / "tests"
    subprocess.run(["nginx", "-s", "quit"], capture_output=True)
    for line in subprocess.run(["ps", "-eo", "pid,comm,args"], capture_output=True, text=True).stdout.splitlines():
        pid, comm, args = (line.split(None, 2) + ["", ""])[:3]
        if comm in ("python3", "runuser") and re.search(r"suveryn-setup (serve|watch)", args):
            os.kill(int(pid), 15)  # an earlier run's
    time.sleep(0.5)
    if REAL:
        for path in ("/var/lib/suveryn/setup", "/etc/suveryn/nginx-setup.conf", "/etc/suveryn/tls/setup-cert.pem",
                     "/etc/nginx/sites-available/suveryn", "/etc/nginx/sites-enabled/suveryn", "/run/suveryn"):
            shutil.rmtree(path, ignore_errors=True) if Path(path).is_dir() else Path(path).unlink(missing_ok=True)
    for path in () if REAL else ("/etc/suveryn", "/var/lib/suveryn/setup", "/etc/nginx/sites-available/suveryn",
                 "/etc/nginx/sites-enabled/suveryn", "/run/suveryn"):
        shutil.rmtree(path, ignore_errors=True) if Path(path).is_dir() and not Path(path).is_symlink() else Path(path).unlink(missing_ok=True)
    Path("/etc/suveryn/tls").mkdir(parents=True, exist_ok=True)
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
    if REAL:  # the real suveryn-provision first-boot and outgoing-traffic check, as a test machine
        env = {**os.environ, "SUVERYN_SETUP_SITE": "test", "SUVERYN_SETUP_SEEN_DONE_WAIT": "300"}
    sites_enabled()
    # Its output to a file, not a pipe: without systemd, prepare starts nginx, which keeps it open.
    with open(OUT / "prepare.log", "w") as log:
        r = subprocess.run([sys.executable, SETUP / "suveryn-setup", "prepare"], env=env, stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if r.returncode != 0:
        sys.exit(f"prepare failed: {(OUT / 'prepare.log').read_text()[-800:]}")
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
    return {"serve": serve, "watch": watch, "host": HOST, "connect": "127.0.0.1", "address": ip, "share": ip,
            "appliance": APPLIANCE, "manifest": OUT / "manifest.yaml", "other": OUT / "other.yaml", "out": OUT,
            "answers": Path("/var/lib/suveryn/setup/answers"), "fails_once": not REAL, "slow": 90 if REAL else 1}


def direct():
    """Straight to this machine (no proxy), trusting its setup certificate as the browser was told to."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))


VIOLATIONS = """window.__csp = []; document.addEventListener('securitypolicyviolation',
  e => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));"""


def flow(browser, m):
    """The screens from Welcome to Complete. `m`: host (<name>.local), connect (the address the browser and
    the header check reach it at), address (shown on Network), share (a share server on the office's
    network), appliance (its ID), manifest and other (manifests for it and for another appliance), out
    (screenshots), answers (the answers folder, when this machine can read it; None for a VM)."""
    HOST, OUT = m["host"], m["out"]
    ctx = browser.new_context(ignore_https_errors=True, accept_downloads=True, locale="en-GB")
    ctx.add_init_script(VIOLATIONS)
    pg = ctx.new_page()
    m["page"] = pg  # for browse() to show where it stopped
    errors, console = [], []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}"))
    m["console"] = console
    pg.goto(f"https://{HOST}/")
    pg.locator("h1").first.wait_for(timeout=m.get("slow", 1) * 30000)
    if m.get("language"):  # the language chosen at installation; the rest of the flow is in English
        lang = pg.evaluate("document.documentElement.lang")
        check("the screens open in the language chosen at installation", lang.startswith(m["language"]), lang)
        if not lang.startswith("en"):
            pg.locator(".ui-select-button").click()
            pg.get_by_role("option", name="English").click()
    pg.get_by_role("heading", name="Set up your sūveryn appliance", level=1).wait_for()
    r = direct().open(urllib.request.Request(f"https://{m['connect']}/", headers={"Host": HOST}), timeout=10)
    csp = r.headers.get("Content-Security-Policy", "")
    check("the setup page has the strict CSP and the security headers", "style-src 'self'" in csp
          and r.headers.get("X-Frame-Options") == "DENY" and r.headers.get("Strict-Transport-Security"))
    pg.get_by_role("button", name="Get started").click()

    pg.get_by_role("heading", name="Network", level=1).wait_for()
    check("Network shows the address in use", m["address"] in pg.content(), m["address"])
    pg.get_by_role("button", name="Continue").click()

    pg.get_by_role("heading", name="Administrator account", level=1).wait_for()
    pg.locator("input[name=name]").fill("Filip Tack")
    pg.locator("input[name=email]").fill("filip@firm.example")
    pg.locator("input[name=password]").fill(PASSWORD)
    pg.locator("input[name=confirm]").fill(PASSWORD)
    pg.get_by_role("button", name="Continue").click()

    pg.get_by_role("heading", name="Certificate", level=1).wait_for()
    if m["answers"]:
        check("the administrator's password is on the appliance once, readable by the setup service only",
              oct(os.stat(m["answers"] / "administrator-password").st_mode & 0o777) == "0o600")
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
    check("Support shows the appliance's ID for registration", m["appliance"] in pg.content())
    drop = pg.locator("input[type=file]").first
    drop.set_input_files(m["other"])
    other = pg.get_by_text("This manifest is for another appliance.")
    other.wait_for()
    check("Support: a manifest for another appliance is refused by the appliance", other.is_visible())
    pg.locator("input[type=file]").first.set_input_files(m["manifest"])
    pg.get_by_text("Sūveryn Enterprise").first.wait_for()
    check("Support: the signed manifest for this appliance is accepted and shown", True)
    pg.get_by_role("button", name="Continue").click()

    pg.get_by_role("heading", name="Backups", level=1).wait_for()
    pg.get_by_text("Network share", exact=True).click()
    pg.locator("input[name=address]").fill("\\\\8.8.8.8\\backups")
    pg.get_by_role("button", name="Test connection").click()
    pg.get_by_text("This address isn't on your own network.").wait_for()
    check("Backups: a share outside the office's network is refused", True)
    pg.locator("input[name=address]").fill(f"\\\\{m['share']}\\backups")
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
    recipient = re.search(r"# public key: (age1\S+)", key).group(1)
    state = json.loads(direct().open(urllib.request.Request(f"https://{m['connect']}/setup/v1/state",
                                                            headers={"Host": HOST}), timeout=10).read())
    secret_kept = m["answers"] and any("AGE-SECRET" in p.read_text(errors="ignore") for p in m["answers"].iterdir())
    check("only the recovery key's public half reached the appliance",
          state["answers"]["backups"]["recipient"] == recipient and "AGE-SECRET-KEY" not in json.dumps(state)
          and not secret_kept)
    pg.screenshot(path=OUT / "6-review.png", full_page=True)
    pg.get_by_role("button", name="Start provisioning").click()

    if m.get("fails_once", True):
        # The stand-in fails once at Keycloak: the screen says so and offers Try again.
        pg.get_by_role("button", name="Try again").wait_for(timeout=m.get("slow", 1) * 30000)
        check("Provisioning: a failed step is shown with Try again",
              pg.locator("[data-state=failed]").count() >= 1)
        pg.screenshot(path=OUT / "7-failed.png", full_page=True)
        pg.get_by_role("button", name="Try again").click()
    pg.get_by_role("heading", name="Setup complete", level=1).wait_for(timeout=m.get("slow", 1) * 60000)
    check("Provisioning runs to Complete through the switch to the real nginx site", True)
    check("Complete shows the sign-in address", f"https://{HOST}" in pg.content())
    pg.screenshot(path=OUT / "8-complete.png", full_page=True)
    check("no CSP violations and no script errors on any screen",
          pg.evaluate("window.__csp.length") == 0 and not errors, f"{pg.evaluate('window.__csp')} {errors}")
    ctx.close()
    if REAL:
        signed_in(browser, m)


def signed_in(browser, m):
    """The administrator from the screens signs in to the chat through Keycloak, once setup has closed."""
    for _ in range(60):
        if Path("/etc/suveryn/setup-complete").exists():
            break
        time.sleep(5)
    ctx = browser.new_context(ignore_https_errors=True, locale="en-GB")
    pg = ctx.new_page()
    try:
        pg.goto(f"https://{m['host']}/")
        pg.locator("#username").wait_for(timeout=60000)
        pg.locator("#username").fill("filip@firm.example")
        pg.locator("#password").fill(PASSWORD)
        pg.locator("#kc-login").click()
        pg.get_by_role("button", name="Sign out").wait_for(timeout=60000)  # the chat's sidebar, signed in
        check("the administrator from the screens signs in to the chat through Keycloak", True)
    except Exception as e:  # noqa: BLE001
        pg.screenshot(path=m["out"] / "9-sign-in.png", full_page=True)
        check("the administrator from the screens signs in to the chat through Keycloak", False, type(e).__name__)
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
    if REAL:
        r = subprocess.run([sys.executable, REPO / "provision" / "suveryn-provision", "verify"], capture_output=True, text=True)
        (OUT / "verify.log").write_text(r.stdout + r.stderr)
        check("suveryn-provision verify passes after the web setup", r.returncode == 0, f"log: {OUT / 'verify.log'}")


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
        # The browser reaches the appliance at m["connect"] (some test machines send everything else through a proxy).
        browser = p.chromium.launch(args=[f"--host-resolver-rules=MAP {m['host']} {m['connect']}", "--no-proxy-server"],
                                    env={k: v for k, v in os.environ.items() if "proxy" not in k.lower()})
        try:
            flow(browser, m)
        except Exception:
            pg = m.get("page")
            if pg and not pg.is_closed():  # where it stopped: a screenshot, the page's text and console
                pg.screenshot(path=m["out"] / "stopped.png", full_page=True)
                (m["out"] / "stopped.txt").write_text(pg.inner_text("body") + "\n\n" + "\n".join(m.get("console", [])))
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
