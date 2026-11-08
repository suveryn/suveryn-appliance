"""Screenshots of the sūveryn login theme, page by page, as a person reaches them.

Starts Keycloak with the built theme and a throwaway realm (screenshots-realm.json: four users and a
public client), walks the eight pages a notary can meet, and writes a PNG per page at 1440 and 390 px
wide, in Dutch, the realm's default language. These are the "code" side of the design boards
(the canvas "Sūveryn · interface", page "Aanmelden"): a change to the theme shows here before it ships.

    uv run --no-project --with playwright --with aiosmtpd keycloak/theme/screenshots/shoot.py
    (once: uv run --no-project --with playwright playwright install chromium)

Needs Java 21 on PATH. Keycloak itself (the version in KEYCLOAK_VERSION) is downloaded once into
keycloak/theme/screenshots/.keycloak/ (git-ignored); set KEYCLOAK_HOME to use an unpacked Keycloak
instead. Uses port 8180 and, for the "e-mail sent" page, a stand-in SMTP server on 2525. The theme must
be built first (build_theme.py). No Docker.
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys, tarfile, time, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYCLOAK_VERSION = os.environ.get("KEYCLOAK_VERSION", "26.8.0")
PORT = 8180
OUT = HERE / "out"
JAR = HERE.parent / "build" / "suveryn-keycloak-theme.jar"
PW = "Welkom-bij-suveryn-1"
PAGES = ("a01-login", "a02-wrong-password", "a03-forgot-password", "a04-email-sent", "a05-two-step-code",
         "a06-two-step-set-up", "a07-new-password", "a08-error")


def keycloak_home() -> Path:
    if os.environ.get("KEYCLOAK_HOME"):
        return Path(os.environ["KEYCLOAK_HOME"])
    home = HERE / ".keycloak" / f"keycloak-{KEYCLOAK_VERSION}"
    if not home.exists():
        url = f"https://github.com/keycloak/keycloak/releases/download/{KEYCLOAK_VERSION}/keycloak-{KEYCLOAK_VERSION}.tar.gz"
        print(f"downloading {url}", file=sys.stderr)
        home.parent.mkdir(parents=True, exist_ok=True)
        tgz = home.parent / "keycloak.tar.gz"
        urllib.request.urlretrieve(url, tgz)
        with tarfile.open(tgz) as t:
            t.extractall(home.parent, filter="data")
        tgz.unlink()
    return home


def start_keycloak(home: Path) -> subprocess.Popen:
    if not JAR.exists():
        sys.exit(f"no theme: build it first (uv run keycloak/theme/build_theme.py), expected {JAR}")
    shutil.copy(JAR, home / "providers" / JAR.name)
    shutil.rmtree(home / "data" / "tmp" / "kc-gzip-cache", ignore_errors=True)  # the theme README: stale compressed copies
    (home / "data" / "import").mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "screenshots-realm.json", home / "data" / "import" / "suveryn-realm.json")
    env = {**os.environ, "KC_BOOTSTRAP_ADMIN_USERNAME": "admin", "KC_BOOTSTRAP_ADMIN_PASSWORD": "admin-for-screenshots-only"}
    kc = subprocess.Popen([str(home / "bin" / "kc.sh"), "start-dev", "--import-realm", f"--http-port={PORT}", "--hostname-strict=false"],
                          env=env, stdout=(HERE / "keycloak.log").open("w"), stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/realms/suveryn/account/", timeout=2).status == 200:
                return kc
        except Exception:
            time.sleep(1)
    kc.terminate()
    sys.exit("Keycloak didn't start: see keycloak/theme/screenshots/keycloak.log")


def auth_url(client: str = "shots") -> str:
    q = urllib.parse.urlencode({"client_id": client, "redirect_uri": f"http://127.0.0.1:{PORT}/realms/suveryn/account/", "response_type": "code",
                                "scope": "openid", "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", "code_challenge_method": "S256"})
    return f"http://127.0.0.1:{PORT}/realms/suveryn/protocol/openid-connect/auth?{q}"


def walk(width: int, tag: str) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": width, "height": 900 if width > 390 else 844}, locale="nl")
        page = ctx.new_page()

        def shot(name: str) -> None:
            page.wait_for_timeout(400)
            page.screenshot(path=str(OUT / f"{name}{tag}.png"), full_page=True)
            print(name + tag)

        def sign_in(user: str, password: str) -> None:
            page.goto(auth_url()); page.fill("#username", user); page.fill("#password", password); page.click("#kc-login"); page.wait_for_load_state()

        page.goto(auth_url()); shot("a01-login")
        sign_in("notaris", "verkeerd"); shot("a02-wrong-password")
        page.click("a[href*='reset-credentials']"); page.wait_for_load_state(); shot("a03-forgot-password")
        page.fill("#username", "notaris@kantoor.test"); page.click("input[type=submit], button[type=submit]"); page.wait_for_load_state(); shot("a04-email-sent")
        sign_in("tweestaps", PW); shot("a05-two-step-code")
        sign_in("instellen", PW); shot("a06-two-step-set-up")
        ctx.clear_cookies()
        sign_in("nieuw", PW); shot("a07-new-password")
        ctx.clear_cookies()
        page.goto(auth_url("nobody")); shot("a08-error")
        ctx.close(); b.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    smtp = subprocess.Popen([sys.executable, "-m", "aiosmtpd", "-n", "-l", "127.0.0.1:2525"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    kc = start_keycloak(keycloak_home())
    try:
        walk(1440, "")
        walk(390, "-390")
    finally:
        kc.terminate(); kc.wait(timeout=30); smtp.terminate()
    print(f"{len(PAGES) * 2} screenshots in {OUT}")


if __name__ == "__main__":
    main()
