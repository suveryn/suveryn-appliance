"""suveryn-backup (tracker #33): a backup made, checked and restored against a real PostgreSQL, with a real
age recovery key. Needs PostgreSQL with pgvector (SUVERYN_TEST_PG_HOST and SUVERYN_TEST_PG_PORT: a socket
folder and port where the superuser postgres connects without a password), age and zstd; skipped without.

    SUVERYN_TEST_PG_HOST=/var/run/postgresql python3 -m pytest backup/tests
"""

import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
TOOL = HERE / "suveryn-backup"
PG_HOST = os.environ.get("SUVERYN_TEST_PG_HOST")
PG_PORT = os.environ.get("SUVERYN_TEST_PG_PORT", "5432")
needs = pytest.mark.skipif(not PG_HOST or not shutil.which("age") or not shutil.which("zstd") or not shutil.which("pg_dump"),
                           reason="needs SUVERYN_TEST_PG_HOST, age, zstd and PostgreSQL's client tools")


def load():
    loader = importlib.machinery.SourceFileLoader("suveryn_backup", str(TOOL))
    spec = importlib.util.spec_from_loader("suveryn_backup", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def psql(sql: str, db: str = "postgres") -> str:
    r = subprocess.run(["psql", "-qAt", "-v", "ON_ERROR_STOP=1", "-h", PG_HOST, "-p", PG_PORT, "-U", "postgres", "-d", db,
                        "-c", sql], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def appliance(tmp_path):
    """Two databases with their owners, the office's files on a fake data partition, a recovery key,
    and backup settings pointing at a folder."""
    tag = uuid.uuid4().hex[:8]
    gw_role, kc_role, gw_db, kc_db = f"t{tag}_gw", f"t{tag}_kc", f"t{tag}_suveryn", f"t{tag}_keycloak"
    for role, pw in ((gw_role, "gw-pass"), (kc_role, "kc-pass")):
        psql(f"CREATE ROLE {role} LOGIN PASSWORD '{pw}'")
    psql(f"CREATE DATABASE {gw_db} OWNER {gw_role}")
    psql(f"CREATE DATABASE {kc_db} OWNER {kc_role}")
    psql("CREATE EXTENSION vector", gw_db)
    psql(f"CREATE TABLE chunks (id int, text text, embedding vector(3)); ALTER TABLE chunks OWNER TO {gw_role};"
         "INSERT INTO chunks VALUES (1, 'Fictional passage one', '[1,2,3]'), (2, 'Fictional passage two', '[4,5,6]')", gw_db)
    psql(f"CREATE TABLE user_entity (id text, username text); ALTER TABLE user_entity OWNER TO {kc_role};"
         "INSERT INTO user_entity VALUES ('a', 'test-alice')", kc_db)
    var = tmp_path / "var"
    (var / "etc" / "tls").mkdir(parents=True)
    (var / "etc" / "gateway.env").write_text(f"SUVERYN_DATABASE_URL=postgresql://{gw_role}:gw-pass@127.0.0.1:5432/{gw_db}\n")
    (var / "etc" / "keycloak.env").write_text("KC_DB_PASSWORD=kc-pass\n")
    (var / "etc" / "tls" / "server.key").write_text("not a real key\n")
    (var / "host").mkdir()
    (var / "host" / "hostname").write_text("test-appliance\n")
    (var / "identity").mkdir()
    (var / "identity" / "sealed").write_text("never in a backup\n")
    key = tmp_path / "recovery.key"
    subprocess.run(["age-keygen", "-o", str(key)], capture_output=True, check=True)
    recipient = next(line.split(": ")[1] for line in key.read_text().splitlines() if line.startswith("# public key"))
    target = tmp_path / "usb"
    target.mkdir()
    config = tmp_path / "backups.json"
    config.write_text(json.dumps({"target": "folder", "path": str(target), "retention_days": 30, "recipient": recipient}))
    env = {**os.environ, "SUVERYN_BACKUP_TEST": "1", "SUVERYN_BACKUP_ALLOW_FOLDER": "1", "SUVERYN_BACKUP_CONFIG": str(config),
           "SUVERYN_BACKUP_VAR": str(var), "SUVERYN_BACKUP_STATE": str(var / "backup"), "SUVERYN_BACKUP_NO_SERVICES": "1",
           "SUVERYN_BACKUP_DATABASES": f"{gw_db}:{gw_role},{kc_db}:{kc_role}", "SUVERYN_BACKUP_KEYCLOAK_ROLE": kc_role,
           "SUVERYN_BACKUP_PG_USER": "root" if os.geteuid() == 0 else "postgres",
           "PGHOST": PG_HOST, "PGPORT": PG_PORT, "PGUSER": "postgres"}
    yield {"env": env, "var": var, "key": key, "target": target, "config": config, "dbs": (gw_db, kc_db),
           "roles": (gw_role, kc_role), "tmp": tmp_path}
    for db in (gw_db, kc_db):
        psql(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
    for role in (gw_role, kc_role):
        psql(f"DROP ROLE IF EXISTS {role}")


def tool(env, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)], env=env, capture_output=True, text=True)


@needs
def test_a_backup_is_made_checked_and_restored_onto_changed_databases(appliance):
    env, var, gw_db, kc_db = appliance["env"], appliance["var"], *appliance["dbs"]
    r = tool(env, "run")
    assert r.returncode == 0, r.stdout + r.stderr
    [backup] = (appliance["target"] / "suveryn-backups" / "test-appliance").glob("*.age")
    assert backup.name.startswith("suveryn-backup_test-appliance_") and not list(backup.parent.glob(".*partial"))
    raw = backup.read_bytes()
    assert raw.startswith(b"age-encryption.org/v1") and b"Fictional passage" not in raw and b"not a real key" not in raw
    status = json.loads((var / "backup" / "status.json").read_text())
    assert status["configured"] and status["last_success"]["file"] == backup.name and "last_error" not in status
    assert oct((var / "backup" / "status.json").stat().st_mode & 0o777) == "0o644"

    r = tool(env, "check", backup, "--identity", appliance["key"])
    assert r.returncode == 0 and r.stdout.startswith("usable: made "), r.stdout
    assert "files: etc, host" in r.stdout

    # The office's data changes after the backup; a replacement machine restores it.
    psql("DELETE FROM chunks", gw_db)
    psql("DELETE FROM user_entity", kc_db)
    (var / "etc" / "gateway.env").write_text("changed\n")
    psql(f"ALTER ROLE {appliance['roles'][0]} PASSWORD 'another'")
    before = psql(f"SELECT rolpassword FROM pg_authid WHERE rolname = '{appliance['roles'][0]}'")
    assert tool(env, "restore", backup, "--identity", appliance["key"]).returncode == 2  # asks for --yes
    r = tool(env, "restore", backup, "--identity", appliance["key"], "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert psql("SELECT text, embedding::text FROM chunks ORDER BY id", gw_db).splitlines() == [
        "Fictional passage one|[1,2,3]", "Fictional passage two|[4,5,6]"]
    assert psql("SELECT username FROM user_entity", kc_db) == "test-alice"
    assert psql(f"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = '{gw_db}'") == appliance["roles"][0]
    assert psql("SELECT tableowner FROM pg_tables WHERE tablename = 'chunks'", gw_db) == appliance["roles"][0]
    assert "gw-pass" in (var / "etc" / "gateway.env").read_text()
    assert psql(f"SELECT rolpassword FROM pg_authid WHERE rolname = '{appliance['roles'][0]}'") != before  # set back
    assert (var / "identity" / "sealed").exists()  # never in a backup, never touched by a restore


@needs
def test_the_wrong_key_reads_nothing(appliance, tmp_path):
    env = appliance["env"]
    assert tool(env, "run").returncode == 0
    [backup] = (appliance["target"] / "suveryn-backups" / "test-appliance").glob("*.age")
    other = tmp_path / "other.key"
    subprocess.run(["age-keygen", "-o", str(other)], capture_output=True, check=True)
    r = tool(env, "check", backup, "--identity", other)
    assert r.returncode == 1 and "not usable" in r.stdout


@needs
def test_a_failed_backup_is_recorded_and_deletes_nothing(appliance):
    env = appliance["env"]
    assert tool(env, "run").returncode == 0
    ok = json.loads((appliance["var"] / "backup" / "status.json").read_text())["last_success"]
    env = {**env, "SUVERYN_BACKUP_DATABASES": "t_does_not_exist:nobody"}
    r = tool(env, "run")
    assert r.returncode == 1 and "backup failed (database)" in r.stdout
    status = json.loads((appliance["var"] / "backup" / "status.json").read_text())
    assert status["last_error"]["step"] == "database" and status["last_success"] == ok
    assert len(list((appliance["target"] / "suveryn-backups" / "test-appliance").glob("*.age"))) == 1


def test_not_set_up_is_said_and_never_a_failure(tmp_path):
    (tmp_path / "backups.json").write_text(json.dumps({"target": "later"}))
    env = {**os.environ, "SUVERYN_BACKUP_TEST": "1", "SUVERYN_BACKUP_CONFIG": str(tmp_path / "backups.json"),
           "SUVERYN_BACKUP_STATE": str(tmp_path / "state")}
    r = tool(env, "run")
    assert r.returncode == 0 and "aren't set up" in r.stdout
    assert json.loads((tmp_path / "state" / "status.json").read_text())["configured"] is False


def test_old_backups_expire_but_the_newest_always_stays(tmp_path):
    b = load()
    at = dt.datetime(2026, 10, 23, 2, 30, tzinfo=dt.timezone.utc)
    for days in (0, 10, 31, 60):
        (tmp_path / f"suveryn-backup_x_{(at - dt.timedelta(days=days)).strftime('%Y%m%dT%H%M%SZ')}.tar.zst.age").write_bytes(b"x")
    (tmp_path / "someone-elses-file.age").write_bytes(b"x")
    assert len(b.expire(tmp_path, 30, at)) == 2
    assert [f["at"] for f in b.backups_in(tmp_path)] == ["2026-10-23T02:30:00Z", "2026-10-13T02:30:00Z"]
    assert (tmp_path / "someone-elses-file.age").exists()
    # only old ones left: the newest stays anyway
    for f in tmp_path.glob("suveryn-backup_*"):
        f.unlink()
    (tmp_path / "suveryn-backup_x_20250101T000000Z.tar.zst.age").write_bytes(b"x")
    assert b.expire(tmp_path, 14, at) == [] and len(b.backups_in(tmp_path)) == 1


def test_the_settings_are_the_web_setups(tmp_path, monkeypatch):
    b = load()
    monkeypatch.setattr(b, "CONFIG", tmp_path / "backups.json")
    good = {"target": "usb", "disk": "usb-X_1", "retention_days": 30,
            "recipient": "age1" + "q" * 58}
    for change, step in (({"recipient": "ssh-rsa AAAA"}, "settings"), ({"retention_days": 7}, "settings"),
                         ({"target": "cloud"}, "settings"), ({"target": "folder", "path": "/tmp"}, "settings")):
        (tmp_path / "backups.json").write_text(json.dumps({**good, **change}))
        with pytest.raises(b.Failed) as e:
            b.settings()
        assert e.value.step == step
    (tmp_path / "backups.json").write_text(json.dumps(good))
    assert b.settings()["disk"] == "usb-X_1"


@needs
def test_a_restore_that_fails_leaves_the_live_database_as_it_was(appliance):
    """VM check of #33: the restore dropped the database before it knew it could restore it."""
    env, gw_db = appliance["env"], appliance["dbs"][0]
    assert tool(env, "run").returncode == 0
    [backup] = (appliance["target"] / "suveryn-backups" / "test-appliance").glob("*.age")
    psql("INSERT INTO chunks VALUES (3, 'Fictional passage three', '[7,8,9]')", gw_db)
    # The second database can't be restored: its owner is gone (renamed), after the first was restored aside.
    kc_role = appliance["roles"][1]
    psql(f"ALTER ROLE {kc_role} RENAME TO {kc_role}_away")
    try:
        r = tool(env, "restore", backup, "--identity", appliance["key"], "--yes")
    finally:
        psql(f"ALTER ROLE {kc_role}_away RENAME TO {kc_role}")
    assert r.returncode == 1 and "restore failed (database)" in r.stdout
    assert psql("SELECT count(*) FROM chunks", gw_db) == "3"  # the live database untouched
    assert psql("SELECT count(*) FROM pg_database WHERE datname LIKE '%\\_restoring'") == "0"  # nothing left aside


@needs
def test_the_dumps_are_readable_whatever_the_umask(appliance):
    env = appliance["env"]
    assert tool(env, "run").returncode == 0
    [backup] = (appliance["target"] / "suveryn-backups" / "test-appliance").glob("*.age")
    r = subprocess.run(["sh", "-c", f'umask 077 && exec "{sys.executable}" "{TOOL}" restore "{backup}" --identity "{appliance["key"]}" --yes'],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_failed_share_mount_leaves_no_password_behind(tmp_path, monkeypatch):
    b = load()
    secret = tmp_path / "secret"
    secret.write_text("not-a-real-password\n")
    monkeypatch.setattr(b, "SHARE_SECRET", secret)
    monkeypatch.setattr(b, "MOUNT", tmp_path / "mnt")
    made = []
    real_mkstemp = b.tempfile.mkstemp

    def mkstemp(prefix, dir):
        fd, path = real_mkstemp(prefix=prefix, dir=tmp_path)
        made.append(Path(path))
        return fd, path
    monkeypatch.setattr(b.tempfile, "mkstemp", mkstemp)

    def refuse(step, cmd, **kw):
        raise b.Failed(step, "mount failed: mount error(13): Permission denied")
    monkeypatch.setattr(b, "run", refuse)
    s = {"target": "share", "protocol": "smb", "address": "//10.0.0.2/backups", "user": "bk"}
    with pytest.raises(b.Failed):
        with b.Target(s):
            pass
    assert made and not any(p.exists() for p in made)


def test_ages_message_is_its_own_without_the_prefix_or_the_bug_report_line():
    b = load()
    raw = (b"age: error: no identity matched any of the recipients\n"
           b"age: report unexpected or unhelpful errors at https://filippo.io/age/report\n")
    assert b.age_error(raw) == "no identity matched any of the recipients"


@needs
def test_the_newest_backup_on_a_target_is_checked_and_restored_by_settings_given(appliance, tmp_path):
    """#56: a replacement machine has only where the backups are and the key, not the office's settings."""
    env = appliance["env"]
    assert tool(env, "run").returncode == 0
    given = tmp_path / "restore-settings.json"
    given.write_text(json.dumps({"target": "folder", "path": str(appliance["target"])}))  # no recipient, no retention
    env = {**env, "SUVERYN_BACKUP_CONFIG": str(tmp_path / "none.json")}
    r = tool(env, "--settings", given, "check", "newest", "--on-target", "--json", "--identity", appliance["key"])
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["usable"] and out["appliance"] == "test-appliance" and out["file"].startswith("suveryn-backup_")
    r = tool(env, "--settings", given, "check", "newest", "--on-target", "--json", "--identity", appliance["tmp"] / "recovery.key.missing")
    assert r.returncode == 1 and json.loads(r.stdout)["step"] == "decrypt"
    psql("DELETE FROM chunks", appliance["dbs"][0])
    r = tool(env, "--settings", given, "restore", "newest", "--on-target", "--yes", "--identity", appliance["key"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert psql("SELECT count(*) FROM chunks", appliance["dbs"][0]) == "2"
    r = tool(env, "--settings", given, "check", "suveryn-backup_x_20990101T000000Z.tar.zst.age", "--on-target", "--json",
             "--identity", appliance["key"])
    assert r.returncode == 1 and json.loads(r.stdout)["step"] == "target"
