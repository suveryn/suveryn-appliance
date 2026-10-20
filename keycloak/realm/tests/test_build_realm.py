"""The production realm (suveryn-tracker#16). Run: python3 -m pytest keycloak/realm/tests

Uses suveryn-core's development realm at the commit in realm-lock.json, from SUVERYN_CORE_REPO or a
sibling ../suveryn-core checkout; skipped when neither has that commit.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import build_realm  # noqa: E402

LOCK = json.loads((HERE / "realm-lock.json").read_text())
CORE = os.environ.get("SUVERYN_CORE_REPO", str(HERE.parents[2] / "suveryn-core"))
URL = "https://suveryn.office.example"


@pytest.fixture(scope="module")
def dev():
    try:
        data, commit = build_realm.fetch_realm(CORE, LOCK["commit"], LOCK["path"])
    except subprocess.CalledProcessError:
        pytest.skip(f"no suveryn-core with commit {LOCK['commit'][:7]} at {CORE}")
    import hashlib
    assert hashlib.sha256(data).hexdigest() == LOCK["sha256"]
    return json.loads(data)


def build(dev, chat_secret="test-secret-not-real"):
    """The production realm, with a directory secret when the realm has that client (cases, stage 2)."""
    return build_realm.production_realm(dev, URL, chat_secret, "directory-secret" if build_realm.has_directory_client(dev) else "")


def chat_client(realm):
    [c] = [c for c in realm["clients"] if c["clientId"] == "suveryn-chat"]
    return c


def test_production_settings_are_laid_over_the_development_realm(dev):
    prod = build(dev)
    assert prod["sslRequired"] == "all" and dev["sslRequired"] == "external"
    assert prod["eventsEnabled"] and prod["eventsExpiration"] == 180 * 86400
    assert prod["adminEventsEnabled"] and not prod["adminEventsDetailsEnabled"]
    assert prod["attributes"]["adminEventsExpiration"] == str(180 * 86400)
    assert (prod["loginTheme"], prod["accountTheme"], prod["emailTheme"]) == ("suveryn",) * 3
    c = chat_client(prod)
    assert c["redirectUris"] == [f"{URL}/auth/callback"] and c["baseUrl"] == f"{URL}/"
    assert c["attributes"]["post.logout.redirect.uris"] == f"{URL}/" and c["webOrigins"] == []
    assert c["secret"] == "test-secret-not-real"
    # The web setup's password rules (15 characters), stricter than development's 12.
    assert prod["passwordPolicy"] == "length(15) and notUsername and notEmail"
    # everything else is the development realm's: flow, roles, brute-force rules
    for key in ("bruteForceProtected", "failureFactor", "accessTokenLifespan", "roles", "scopeMappings",
                "registrationAllowed", "identityProviders", "supportedLocales"):
        assert prod[key] == dev[key], key
    for key in ("standardFlowEnabled", "implicitFlowEnabled", "directAccessGrantsEnabled", "publicClient", "fullScopeAllowed"):
        assert c[key] == chat_client(dev)[key], key


DIRECTORY = {"clientId": "suveryn-directory", "enabled": True, "protocol": "openid-connect", "publicClient": False,
             "standardFlowEnabled": False, "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
             "serviceAccountsEnabled": True, "redirectUris": [], "webOrigins": [],
             "attributes": {"pkce.code.challenge.method": "S256"}}
SERVICE_ACCOUNT = {"username": "service-account-suveryn-directory", "enabled": True,
                   "serviceAccountClientId": "suveryn-directory", "clientRoles": {"realm-management": ["view-users", "view-realm"]}}
SCOPE = {"realm-management": [{"client": "suveryn-directory", "roles": ["view-users", "view-realm"]}]}


def test_the_directory_client_gets_its_own_secret_and_may_only_view_users(dev):
    """suveryn-tracker#10, stage 2: suveryn-core's realm carries a second client, suveryn-directory,
    whose service account reads the office's accounts for a case's members. Shaped here as that
    realm has it, so the test holds whatever commit realm-lock.json points at."""
    with_dir = dev if build_realm.has_directory_client(dev) else {**dev, "clients": [*dev["clients"], DIRECTORY], "users": [SERVICE_ACCOUNT],
                                                                  "clientScopeMappings": SCOPE}
    without = {**dev, "clients": [chat_client(dev)], "users": []}
    prod = build_realm.production_realm(with_dir, URL, "chat-secret", "directory-secret")
    chat, directory = prod["clients"]
    assert chat["secret"] == "chat-secret" and directory["secret"] == "directory-secret"
    assert directory["redirectUris"] == [] and not directory["standardFlowEnabled"]
    assert not chat["serviceAccountsEnabled"]
    with pytest.raises(SystemExit):
        build_realm.production_realm(with_dir, URL, "chat-secret")  # the client without a secret: refused
    # a service account with more rights, on the chat client, or a directory client that could sign in: refused
    for bad in ({"users": [{**SERVICE_ACCOUNT, "clientRoles": {"realm-management": ["manage-users"]}}]},
                {"users": [{**SERVICE_ACCOUNT, "clientRoles": {"realm-management": ["view-users", "view-realm", "manage-realm"]}}]},
                {"users": [{**SERVICE_ACCOUNT, "clientRoles": {"realm-management": ["view-users"], "account": ["manage-account"]}}]},
                {"users": [{**SERVICE_ACCOUNT, "serviceAccountClientId": "suveryn-chat"}]},
                {"clients": [{**chat_client(dev), "serviceAccountsEnabled": True}, DIRECTORY]},
                {"clients": [chat_client(dev), {**DIRECTORY, "standardFlowEnabled": True}]}):
        with pytest.raises(SystemExit):
            build_realm.production_realm({**with_dir, **bad}, URL, "chat-secret", "directory-secret")
    assert not build_realm.has_directory_client(without) and build_realm.has_directory_client(with_dir)
    assert "secret" not in json.dumps(build_realm.production_realm(without, URL, "chat-secret")["clients"][1:])  # builds without it


def test_the_administrator_from_the_web_setup_signs_in_by_email_with_only_a_hash_in_the_file():
    import base64
    import hashlib
    u = build_realm.administrator_user("  Anne  de  Smet ", "Anne@Office.BE", "a long test password 1", salt=b"0" * 16)
    assert (u["username"], u["email"], u["firstName"], u["lastName"]) == ("anne@office.be", "anne@office.be", "Anne", "de Smet")
    assert u["realmRoles"] == ["suveryn-admin"] and u["enabled"] and u["emailVerified"]
    assert set(u["clientRoles"]["realm-management"]) == {"manage-users", "view-users", "query-users", "query-groups"}
    assert "a long test password 1" not in json.dumps(u)
    [c] = u["credentials"]
    secret, data = json.loads(c["secretData"]), json.loads(c["credentialData"])
    assert data == {"hashIterations": 210000, "algorithm": "pbkdf2-sha512", "additionalParameters": {}}
    expected = hashlib.pbkdf2_hmac("sha512", b"a long test password 1", b"0" * 16, 210000)
    assert base64.b64decode(secret["value"]) == expected and base64.b64decode(secret["salt"]) == b"0" * 16
    assert build_realm.administrator_user("Cher", "cher@office.be", "x" * 15)["lastName"] == ""
    with pytest.raises(SystemExit):
        build_realm.administrator_user("Anne", "anne@office.be", "too short")


def test_a_production_realm_with_a_weaker_password_policy_is_refused(dev):
    prod = build(dev)
    with pytest.raises(SystemExit, match="password policy"):
        build_realm.verify({**prod, "passwordPolicy": "length(12) and notUsername and notEmail"}, URL)


@pytest.mark.parametrize("change, problem", [
    (lambda r: r.update(registrationAllowed=True), "self-registration"),
    (lambda r: r.update(bruteForceProtected=False), "brute-force"),
    (lambda r: r["clients"][0].update(directAccessGrantsEnabled=True), "password grant"),
    (lambda r: r["clients"][0]["attributes"].update({"pkce.code.challenge.method": "plain"}), "PKCE"),
])
def test_a_weakened_development_realm_is_refused(dev, change, problem):
    weak = json.loads(json.dumps(dev))
    change(weak)
    with pytest.raises(SystemExit, match=problem):
        build(weak, "x")


@pytest.mark.parametrize("url", ["http://suveryn.office.example", "https://*.office.example", "https://x.example/path",
                                 "https://x.example/?a=1", "suveryn.office.example"])
def test_only_a_plain_https_url_is_accepted(url):
    with pytest.raises(SystemExit, match="--public-url"):
        build_realm.check_public_url(url)
    assert build_realm.check_public_url("https://suveryn.office.example/") == "https://suveryn.office.example"


def test_the_build_writes_private_files_with_a_fresh_secret(dev, tmp_path):
    env = {**os.environ, "SUVERYN_CORE_REPO": CORE}
    for out in ("a", "b"):
        subprocess.run([sys.executable, str(HERE / "build_realm.py"), "--public-url", URL, "--out", str(tmp_path / out)],
                       check=True, env=env, capture_output=True)
    secrets = []
    for out in ("a", "b"):
        realm, gw = tmp_path / out / "suveryn-realm.json", tmp_path / out / "gateway-oidc.env"
        assert stat.S_IMODE(realm.stat().st_mode) == stat.S_IMODE(gw.stat().st_mode) == 0o600
        values = dict(line.split("=", 1) for line in gw.read_text().splitlines())
        assert values["SUVERYN_OIDC_ISSUER"] == f"{URL}/idp/realms/suveryn" and values["SUVERYN_PUBLIC_URL"] == URL
        assert json.loads(realm.read_text())["clients"][0]["secret"] == values["SUVERYN_OIDC_CLIENT_SECRET"]
        secrets.append(values["SUVERYN_OIDC_CLIENT_SECRET"])
    assert secrets[0] != secrets[1] and len(secrets[0]) >= 40  # new for every build


def test_built_files_can_never_be_committed(tmp_path):
    """They hold the client secret: git ignores them wherever they are written in this folder."""
    for name in ("suveryn-realm.json", "gateway-oidc.env"):
        r = subprocess.run(["git", "-C", str(HERE), "check-ignore", "-q", str(HERE / name)])
        assert r.returncode == 0, name
    assert "secret" not in (HERE / "realm-lock.json").read_text()


def test_the_directory_client_must_carry_view_users_in_its_scope_and_nothing_more(dev):
    """Pod check of 5e9a539: with fullScopeAllowed off and no scope mapping, the directory client's token
    had no roles and Keycloak refused the user list (403). A realm without the mapping is refused, and so
    is one that maps more, or maps to another client."""
    with_dir = {**dev, "clients": [chat_client(dev), DIRECTORY], "users": [SERVICE_ACCOUNT]}
    build_realm.production_realm({**with_dir, "clientScopeMappings": SCOPE}, URL, "c", "d")
    for bad in ({}, {"realm-management": [{"client": "suveryn-directory", "roles": ["view-realm"]}]},
                {"realm-management": [{"client": "suveryn-directory", "roles": ["view-users", "manage-users"]}]},
                {"realm-management": [*SCOPE["realm-management"], {"client": "suveryn-chat", "roles": ["view-users"]}]}):
        with pytest.raises(SystemExit, match="scope"):
            build_realm.production_realm({**with_dir, "clientScopeMappings": bad}, URL, "c", "d")
