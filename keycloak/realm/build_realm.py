"""Build the production Keycloak realm from suveryn-core's development realm (suveryn-tracker#16).

    python3 keycloak/realm/build_realm.py --public-url https://suveryn.office.example --out <dir>
    python3 keycloak/realm/build_realm.py --update <commit-or-tag>   # follow another suveryn-core commit

One source: the realm in suveryn-core (packages/api-gateway/keycloak/suveryn-realm.json) defines the
client, its flow, the roles and the password and brute-force rules, for development and production
alike. This script takes it at the commit pinned in realm-lock.json (checked by SHA-256) and lays
the production settings over it (PRODUCTION below). Writes, both 0600 and only for provisioning:

- ``suveryn-realm.json``: to import into Keycloak once (``kc.sh import`` or the admin console), then
  delete: it contains the client secret;
- ``gateway-oidc.env``: SUVERYN_OIDC_ISSUER, SUVERYN_OIDC_CLIENT_ID, SUVERYN_OIDC_CLIENT_SECRET and
  SUVERYN_PUBLIC_URL for the gateway.

The client secret is new for every build: rotating it means building again and re-importing the
client (suveryn-core's login cookie key is derived from it, so only sign-ins in progress restart).

Set SUVERYN_CORE_REPO to a local mirror to build without internet access. Python 3.11+, stdlib only.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
LOCK = HERE / "realm-lock.json"
EVENT_RETENTION_S = 180 * 24 * 3600  # 180 days: decided 2026-10-11 (suveryn-tracker#16)

# What production changes, and why. Everything else comes from the development realm unchanged.
PRODUCTION = {
    # "external" lets clients on private addresses (10/8, 172.16/12, 192.168/16) use plain HTTP; in an
    # office every workstation is on one, so only "all" really requires TLS.
    "sslRequired": "all",
    # Sign-in events (logins, failures, lock-outs) and admin events (changes to users, roles, clients),
    # kept 180 days: long enough to investigate an incident found months later, short enough for data
    # minimisation. They hold username, IP address and time, never document content. Admin event
    # details (the changed representation) are off: who changed what is enough.
    "eventsEnabled": True,
    "eventsExpiration": EVENT_RETENTION_S,
    "adminEventsEnabled": True,
    "adminEventsDetailsEnabled": False,
    # The appliance's own theme (keycloak/theme).
    "loginTheme": "suveryn",
    "accountTheme": "suveryn",
    "emailTheme": "suveryn",
}


def fail(message: str):
    raise SystemExit(f"build_realm: {message}")


def fetch_realm(repo: str, commit: str, path: str) -> tuple[bytes, str]:
    if not re.fullmatch(r"[\w][\w./-]*", commit) or repo.startswith("-"):
        fail(f"not a repository and commit: {repo} {commit}")
    if os.path.isdir(repo):
        repo = os.path.abspath(repo)  # git runs in a temporary folder: a relative path would point there
    with tempfile.TemporaryDirectory(prefix="suveryn-core-") as tmp:
        def git(*args: str) -> str:
            return subprocess.run(["git", "-C", tmp, *args], check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-q")
        git("fetch", "-q", "--depth", "1", repo, commit)
        resolved = git("rev-parse", "FETCH_HEAD")
        data = subprocess.run(["git", "-C", tmp, "show", f"FETCH_HEAD:{path}"], check=True, capture_output=True).stdout
        return data, resolved


def check_issuer_base(url: str) -> str:
    """Keycloak's public base URL: https, may have a path (keycloak.conf.example puts it at /idp)."""
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.query or u.fragment or "*" in url:
        fail(f"--issuer-base must be an https URL like https://suveryn.office.example/idp, not {url!r}")
    return f"https://{u.netloc}{u.path.rstrip('/')}"


def check_public_url(url: str) -> str:
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.path not in ("", "/") or u.query or u.fragment or "*" in url:
        fail(f"--public-url must be the https URL people open sūveryn at, like https://suveryn.office.example, not {url!r}")
    return f"https://{u.netloc}"


def production_realm(dev: dict, public_url: str, client_secret: str) -> dict:
    """The development realm with the production settings laid over it. Pure: no I/O."""
    realm = {**dev, **PRODUCTION}
    realm["attributes"] = {**dev.get("attributes", {}), "adminEventsExpiration": str(EVENT_RETENTION_S)}
    [client] = [c for c in dev["clients"] if c["clientId"] == "suveryn-chat"]
    client = {**client, "secret": client_secret, "baseUrl": f"{public_url}/",
              # Exact URLs only, never wildcards: a wildcard redirect lets a crafted link send the code elsewhere.
              "redirectUris": [f"{public_url}/auth/callback"], "webOrigins": [],
              "attributes": {**client["attributes"], "post.logout.redirect.uris": f"{public_url}/"}}
    realm["clients"] = [client if c["clientId"] == "suveryn-chat" else c for c in dev["clients"]]
    verify(realm, public_url)
    return realm


def verify(realm: dict, public_url: str) -> None:
    """Refuse a realm that doesn't meet the production rules, whatever the development realm says."""
    problems = []
    if realm.get("sslRequired") != "all":
        problems.append("sslRequired is not 'all'")
    if not (realm.get("eventsEnabled") and realm.get("adminEventsEnabled")):
        problems.append("sign-in or admin events are off")
    if realm.get("registrationAllowed") or realm.get("identityProviders"):
        problems.append("self-registration or external identity providers are on")
    if not realm.get("bruteForceProtected"):
        problems.append("brute-force protection is off")
    for c in realm["clients"]:
        urls = [*c.get("redirectUris", []), c.get("attributes", {}).get("post.logout.redirect.uris", ""), c.get("baseUrl", "")]
        if any("*" in u or (u and not u.startswith(public_url + "/")) for u in urls):
            problems.append(f"client {c['clientId']} has a wildcard or a URL outside {public_url}")
        if c.get("implicitFlowEnabled") or c.get("directAccessGrantsEnabled") or c.get("publicClient"):
            problems.append(f"client {c['clientId']} allows the implicit flow, password grant or is public")
        if c.get("attributes", {}).get("pkce.code.challenge.method") != "S256":
            problems.append(f"client {c['clientId']} doesn't require PKCE S256")
    if problems:
        fail("refusing to build a production realm: " + "; ".join(problems))


def write_private(path: Path, text: str) -> None:
    path.unlink(missing_ok=True)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        f.write(text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--public-url", help="the https URL people open sūveryn at (also Keycloak's, under /realms)")
    ap.add_argument("--issuer-base", help="Keycloak's public base URL; default <public-url>/idp, as in keycloak.conf.example")
    ap.add_argument("--out", type=Path, help="folder to write suveryn-realm.json and gateway-oidc.env to (0600)")
    ap.add_argument("--update", metavar="REF", help="move realm-lock.json to this suveryn-core commit, tag or branch")
    a = ap.parse_args()
    lock = json.loads(LOCK.read_text())
    repo = os.environ.get("SUVERYN_CORE_REPO", lock["repo"])
    if a.update:
        data, commit = fetch_realm(repo, a.update, lock["path"])
        LOCK.write_text(json.dumps({**lock, "commit": commit, "sha256": hashlib.sha256(data).hexdigest()}, indent=2) + "\n")
        print(f"realm-lock.json: suveryn-core@{commit[:7]}")
        return
    if not (a.public_url and a.out):
        ap.error("--public-url and --out are required")
    public_url = check_public_url(a.public_url)
    issuer_base = check_issuer_base(a.issuer_base or f"{public_url}/idp")
    data, commit = fetch_realm(repo, lock["commit"], lock["path"])
    if commit != lock["commit"] or hashlib.sha256(data).hexdigest() != lock["sha256"]:
        fail("suveryn-core's realm differs from realm-lock.json")
    secret = secrets.token_urlsafe(32)
    realm = production_realm(json.loads(data), public_url, secret)
    a.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(a.out / "suveryn-realm.json", json.dumps(realm, indent=2, ensure_ascii=False) + "\n")
    write_private(a.out / "gateway-oidc.env", "".join(f"{k}={v}\n" for k, v in {
        "SUVERYN_OIDC_ISSUER": f"{issuer_base}/realms/{realm['realm']}", "SUVERYN_OIDC_CLIENT_ID": "suveryn-chat",
        "SUVERYN_OIDC_CLIENT_SECRET": secret, "SUVERYN_PUBLIC_URL": public_url}.items()))
    print(f"production realm from suveryn-core@{commit[:7]} written to {a.out} (0600). Import suveryn-realm.json, "
          "then delete it: it holds the client secret.", file=sys.stderr)


if __name__ == "__main__":
    main()
