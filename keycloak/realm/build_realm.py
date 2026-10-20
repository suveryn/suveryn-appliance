"""Build the production Keycloak realm from suveryn-core's development realm (suveryn-tracker#16).

    python3 keycloak/realm/build_realm.py --public-url https://suveryn.office.example --out <dir>
    python3 keycloak/realm/build_realm.py --update <commit-or-tag>   # follow another suveryn-core commit

One source: the realm in suveryn-core (packages/api-gateway/keycloak/suveryn-realm.json) defines the
client, its flow, the roles and the password and brute-force rules, for development and production
alike. This script takes it at the commit pinned in realm-lock.json (checked by SHA-256) and lays
the production settings over it (PRODUCTION below). Writes, both 0600 and only for provisioning:

- ``suveryn-realm.json``: to import into Keycloak once (``kc.sh import`` or the admin console), then
  delete: it contains the client secret (and, with ``--administrator``, the office's administrator
  from the web setup, whose password is in it only as a pbkdf2-sha512 hash);
- ``gateway-oidc.env``: SUVERYN_OIDC_ISSUER, SUVERYN_OIDC_CLIENT_ID, SUVERYN_OIDC_CLIENT_SECRET and
  SUVERYN_PUBLIC_URL for the gateway.

The client secret is new for every build: rotating it means building again and re-importing the
client (suveryn-core's login cookie key is derived from it, so only sign-ins in progress restart).

Set SUVERYN_CORE_REPO to a local mirror to build without internet access. Python 3.11+, stdlib only.
"""

import argparse
import base64
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
    # The administrator account's rules from the web setup (suveryn-tracker#37), for every account:
    # at least 15 characters, not the user name or email address. The leaked-password list
    # (passwordBlacklist) joins once it exists on the appliance (open point, #37).
    "passwordPolicy": "length(15) and notUsername and notEmail",
}
MIN_PASSWORD = 15
# What the office's administrator may do in Keycloak's admin console for this realm: manage the
# office's own accounts. Nothing on the realm's settings, clients or other realms.
ADMIN_CLIENT_ROLES = ["manage-users", "view-users", "query-users", "query-groups"]
DIRECTORY_ROLES = {"view-users", "view-realm"}  # the directory service account: read-only, nothing else (avatars: view-realm lists who administers)
# Keycloak's pbkdf2-sha512 credential (its default before Keycloak 24, still read by 26): the
# password never appears in the realm file, only its hash.
HASH_ITERATIONS = 210_000


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


DIRECTORY_CLIENT = "suveryn-directory"  # the gateway's read-only view of the office's accounts (suveryn-tracker#10)


def production_realm(dev: dict, public_url: str, client_secret: str, directory_secret: str = "") -> dict:
    """The development realm with the production settings laid over it. Pure: no I/O.

    ``directory_secret`` is the ``suveryn-directory`` client's, when the development realm has that
    client (suveryn-core with cases, stage 2); its service account may only view users."""
    realm = {**dev, **PRODUCTION}
    realm["attributes"] = {**dev.get("attributes", {}), "adminEventsExpiration": str(EVENT_RETENTION_S)}
    [client] = [c for c in dev["clients"] if c["clientId"] == "suveryn-chat"]
    client = {**client, "secret": client_secret, "baseUrl": f"{public_url}/",
              # Exact URLs only, never wildcards: a wildcard redirect lets a crafted link send the code elsewhere.
              "redirectUris": [f"{public_url}/auth/callback"], "webOrigins": [],
              "attributes": {**client["attributes"], "post.logout.redirect.uris": f"{public_url}/"}}
    clients = []
    for c in dev["clients"]:
        if c["clientId"] == "suveryn-chat":
            clients.append(client)
        elif c["clientId"] == DIRECTORY_CLIENT:
            if not directory_secret:
                fail(f"the realm has the {DIRECTORY_CLIENT} client but no secret was given for it")
            clients.append({**c, "secret": directory_secret, "redirectUris": [], "webOrigins": []})
        else:
            clients.append(c)
    realm["clients"] = clients
    verify(realm, public_url)
    return realm


def has_directory_client(dev: dict) -> bool:
    return any(c["clientId"] == DIRECTORY_CLIENT for c in dev["clients"])


def administrator_user(name: str, email: str, password: str, salt: bytes | None = None) -> dict:
    """The office's administrator from the web setup: realm role suveryn-admin and user management.
    Signs in with the email address (the user name is the same address, in lower case)."""
    name, email = " ".join(name.split()), email.strip().lower()
    if not name or "@" not in email or len(password) < MIN_PASSWORD:
        fail("the administrator needs a name, an email address and a password of at least 15 characters")
    # First name up to the first space: "Anne de Smet" is Anne, de Smet (Belgian surnames carry their particles).
    first, _, last = name.partition(" ")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, HASH_ITERATIONS)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return {
        "username": email, "email": email, "emailVerified": True, "enabled": True,
        "firstName": first, "lastName": last,
        "realmRoles": ["suveryn-admin"], "clientRoles": {"realm-management": ADMIN_CLIENT_ROLES},
        "credentials": [{
            "type": "password", "userLabel": "web setup",
            "secretData": json.dumps({"value": b64(digest), "salt": b64(salt), "additionalParameters": {}}),
            "credentialData": json.dumps({"hashIterations": HASH_ITERATIONS, "algorithm": "pbkdf2-sha512",
                                          "additionalParameters": {}}),
        }],
    }


def verify(realm: dict, public_url: str) -> None:
    """Refuse a realm that doesn't meet the production rules, whatever the development realm says."""
    problems = []
    if realm.get("sslRequired") != "all":
        problems.append("sslRequired is not 'all'")
    if not (realm.get("eventsEnabled") and realm.get("adminEventsEnabled")):
        problems.append("sign-in or admin events are off")
    if realm.get("registrationAllowed") or realm.get("identityProviders"):
        problems.append("self-registration or external identity providers are on")
    m = re.search(r"\blength\((\d+)\)", realm.get("passwordPolicy", ""))
    if not m or int(m.group(1)) < MIN_PASSWORD or "notUsername" not in realm["passwordPolicy"]:
        problems.append(f"the password policy doesn't require {MIN_PASSWORD} characters and not the user name")
    if not realm.get("bruteForceProtected"):
        problems.append("brute-force protection is off")
    for c in realm["clients"]:
        urls = [*c.get("redirectUris", []), c.get("attributes", {}).get("post.logout.redirect.uris", ""), c.get("baseUrl", "")]
        if any("*" in u or (u and not u.startswith(public_url + "/")) for u in urls):
            problems.append(f"client {c['clientId']} has a wildcard or a URL outside {public_url}")
        if c.get("implicitFlowEnabled") or c.get("directAccessGrantsEnabled") or c.get("publicClient"):
            problems.append(f"client {c['clientId']} allows the implicit flow, password grant or is public")
        if c.get("serviceAccountsEnabled") and c["clientId"] != DIRECTORY_CLIENT:
            problems.append(f"client {c['clientId']} has a service account")
        if c["clientId"] == DIRECTORY_CLIENT and (c.get("standardFlowEnabled") or c.get("redirectUris")):
            problems.append(f"client {c['clientId']} must have no sign-in flow of its own")
        if c.get("attributes", {}).get("pkce.code.challenge.method") != "S256":
            problems.append(f"client {c['clientId']} doesn't require PKCE S256")
    for u in realm.get("users", []):
        if u.get("serviceAccountClientId"):
            roles = set(u.get("clientRoles", {}).get("realm-management", [])) if u.get("clientRoles", {}).keys() <= {"realm-management"} else None
            if u["serviceAccountClientId"] != DIRECTORY_CLIENT or roles is None or not roles or not roles <= DIRECTORY_ROLES:
                problems.append(f"service account {u.get('username')} has a role beyond {', '.join(sorted(DIRECTORY_ROLES))}, or belongs to another client")
    # The directory client's token carries only roles mapped to its scope (fullScopeAllowed off): without
    # view-users there Keycloak refuses the user list, and members can't be added (Pod check of d1f1918).
    # Nothing else may be mapped, to it or to another client.
    mapped: dict[str, set[str]] = {}
    for client_id, entries in realm.get("clientScopeMappings", {}).items():
        for e in entries:
            mapped.setdefault(e.get("client", ""), set()).update(f"{client_id}/{r}" for r in e.get("roles", []))
    allowed = {f"realm-management/{r}" for r in DIRECTORY_ROLES}
    if any(c["clientId"] == DIRECTORY_CLIENT for c in realm["clients"]):
        roles = mapped.get(DIRECTORY_CLIENT, set())
        if "realm-management/view-users" not in roles:
            problems.append(f"client {DIRECTORY_CLIENT} has no view-users in its scope: the user list would be refused")
    if any(client != DIRECTORY_CLIENT or not roles <= allowed for client, roles in mapped.items()):
        problems.append(f"a client scope mapping beyond {', '.join(sorted(DIRECTORY_ROLES))} for {DIRECTORY_CLIENT}")
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
    ap.add_argument("--administrator", type=Path, metavar="FILE",
                    help="JSON {name, email, password}: the office's administrator, added to the realm (web setup)")
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
    dev = json.loads(data)
    directory_secret = secrets.token_urlsafe(32) if has_directory_client(dev) else ""
    realm = production_realm(dev, public_url, secret, directory_secret)
    if a.administrator:
        admin = json.loads(a.administrator.read_text())
        realm["users"] = [*realm.get("users", []), administrator_user(admin["name"], admin["email"], admin["password"])]
    a.out.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(a.out / "suveryn-realm.json", json.dumps(realm, indent=2, ensure_ascii=False) + "\n")
    write_private(a.out / "gateway-oidc.env", "".join(f"{k}={v}\n" for k, v in {
        "SUVERYN_OIDC_ISSUER": f"{issuer_base}/realms/{realm['realm']}", "SUVERYN_OIDC_CLIENT_ID": "suveryn-chat",
        "SUVERYN_OIDC_CLIENT_SECRET": secret, "SUVERYN_PUBLIC_URL": public_url,
        **({"SUVERYN_DIRECTORY_CLIENT_ID": DIRECTORY_CLIENT, "SUVERYN_DIRECTORY_CLIENT_SECRET": directory_secret}
           if directory_secret else {})}.items()))
    print(f"production realm from suveryn-core@{commit[:7]} written to {a.out} (0600). Import suveryn-realm.json, "
          "then delete it: it holds the client secret.", file=sys.stderr)


if __name__ == "__main__":
    main()
