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
import tempfile
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


@needs
def test_a_replacement_with_a_name_of_its_own_finds_the_offices_backups(appliance, tmp_path):
    """VM check of #57: the replacement looked only under its own name and said "there is no backup on the
    target". It looks under every appliance's name, takes the newest, and never follows a link."""
    env, var = appliance["env"], appliance["var"]
    assert tool(env, "run").returncode == 0
    (var / "host" / "hostname").write_text("suveryn-4f2a\n")  # the replacement, before the restore names it
    given = tmp_path / "restore-settings.json"
    given.write_text(json.dumps({"target": "folder", "path": str(appliance["target"])}))
    env = {**env, "SUVERYN_BACKUP_CONFIG": str(tmp_path / "none.json")}
    r = tool(env, "--settings", given, "check", "newest", "--on-target", "--json", "--identity", appliance["key"])
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["usable"] and out["appliance"] == "test-appliance", r.stdout
    # an older appliance's backup, and a link to somewhere else, are passed over
    older = appliance["target"] / "suveryn-backups" / "old-appliance"
    older.mkdir()
    newest = out["file"]
    shutil.copy(appliance["target"] / "suveryn-backups" / "test-appliance" / newest,
                older / "suveryn-backup_old-appliance_20200101T000000Z.tar.zst.age")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.copy(older / "suveryn-backup_old-appliance_20200101T000000Z.tar.zst.age",
                elsewhere / "suveryn-backup_linked_20990101T000000Z.tar.zst.age")
    (appliance["target"] / "suveryn-backups" / "linked").symlink_to(elsewhere)
    r = tool(env, "--settings", given, "check", "newest", "--on-target", "--json", "--identity", appliance["key"])
    assert json.loads(r.stdout)["file"] == newest
    r = tool(env, "--settings", given, "check", "suveryn-backup_old-appliance_20200101T000000Z.tar.zst.age", "--on-target",
             "--json", "--identity", appliance["key"])
    assert r.returncode == 0 and json.loads(r.stdout)["usable"], r.stdout  # an older one, by name, under its own folder
    r = tool(env, "--settings", given, "check", "suveryn-backup_linked_20990101T000000Z.tar.zst.age", "--on-target", "--json",
             "--identity", appliance["key"])
    assert r.returncode == 1 and json.loads(r.stdout)["step"] == "target"
    psql("DELETE FROM chunks", appliance["dbs"][0])
    r = tool(env, "--settings", given, "restore", "newest", "--on-target", "--yes", "--identity", appliance["key"])
    assert r.returncode == 0 and psql("SELECT count(*) FROM chunks", appliance["dbs"][0]) == "2", r.stdout


# ---------------------------------------------------------------------------- a backup someone else made
# Anyone with the office's public key can make a file the recovery key opens (the audit of 25 October
# 2026, findings 2 and 8). Restoring must act only on what this appliance expects, and change the live
# system all at once or not at all.
import tarfile  # noqa: E402


def remade(appliance, backup: Path, change) -> Path:
    """The backup decrypted, unpacked, changed by ``change(folder, manifest)`` (which returns the manifest
    to write), packed and encrypted again for the office's public key: what someone with that key can make."""
    tmp = appliance["tmp"] / f"remade-{uuid.uuid4().hex[:6]}"
    tmp.mkdir()
    plain = subprocess.run(f'age -d -i "{appliance["key"]}" "{backup}" | zstd -q -d -c', shell=True, capture_output=True, check=True).stdout
    tar_path = tmp / "in.tar"
    tar_path.write_bytes(plain)
    folder = tmp / "x"
    with tarfile.open(tar_path) as t:
        t.extractall(folder, filter="data")
    manifest = change(folder, json.loads((folder / "manifest.json").read_text()))
    (folder / "manifest.json").write_text(json.dumps(manifest))
    out_tar = tmp / "out.tar"
    with tarfile.open(out_tar, "w", format=tarfile.PAX_FORMAT) as t:
        t.add(folder / "manifest.json", arcname="manifest.json")
        for p in sorted(folder.iterdir()):
            if p.name != "manifest.json":
                t.add(p, arcname=p.name)
    recipient = json.loads(appliance["config"].read_text())["recipient"]
    out = appliance["target"] / f"crafted-{uuid.uuid4().hex[:6]}.tar.zst.age"
    subprocess.run(f'zstd -q -c "{out_tar}" | age -r {recipient} -o "{out}"', shell=True, check=True)
    return out


def first_backup(appliance) -> Path:
    assert tool(appliance["env"], "run").returncode == 0
    return next((appliance["target"] / "suveryn-backups" / "test-appliance").glob("*.age"))


@needs
@pytest.mark.parametrize("change, says", [
    (lambda d, m: {**m, "files": ["../../outside"]}, "folders this appliance doesn't back up"),
    (lambda d, m: {**m, "files": ["etc", "etc"]}, "folders this appliance doesn't back up"),
    (lambda d, m: {**m, "databases": {**m["databases"], 'x"; DROP DATABASE postgres; --': "postgres"}}, "databases aren't this appliance's"),
    (lambda d, m: {**m, "databases": {}}, "databases aren't this appliance's"),
    (lambda d, m: {**m, "appliance": "evil; rm -rf /"}, "name or date"),
    (lambda d, m: {**m, "created": "yesterday"}, "name or date"),
    (lambda d, m: [m], "manifest isn't readable"),
    (lambda d, m: {**m, "postgresql": "18; DROP"}, "PostgreSQL version isn't one"),
    (lambda d, m: {**m, "postgresql": True}, "PostgreSQL version isn't one"),
])
def test_a_manifest_that_isnt_this_appliances_is_refused_before_anything_changes(appliance, change, says):
    env, var, gw_db = appliance["env"], appliance["var"], appliance["dbs"][0]
    crafted = remade(appliance, first_backup(appliance), change)
    outside = appliance["tmp"] / "outside"
    outside.mkdir()
    (outside / "keep").write_text("never touched\n")
    psql("INSERT INTO chunks VALUES (3, 'Fictional passage three', '[7,8,9]')", gw_db)
    r = tool(env, "check", crafted, "--identity", appliance["key"])
    assert r.returncode == 1 and says in r.stdout, r.stdout
    r = tool(env, "restore", crafted, "--identity", appliance["key"], "--yes")
    assert r.returncode == 1 and "restore failed (read)" in r.stdout and says in r.stdout, r.stdout
    assert (outside / "keep").exists() and (var / "etc" / "gateway.env").exists()
    assert psql("SELECT count(*) FROM chunks", gw_db) == "3"


@needs
def test_a_backup_records_its_postgresql_and_one_from_a_newer_postgresql_is_refused(appliance):
    """#57: 24.04's PostgreSQL 16, 26.04's 18. A dump restores into the same or a newer version only."""
    env, gw_db = appliance["env"], appliance["dbs"][0]
    backup = first_backup(appliance)
    here = int(psql("SHOW server_version_num")) // 10000
    plain = subprocess.run(f'age -d -i "{appliance["key"]}" "{backup}" | zstd -q -d -c | tar -xOf - manifest.json',
                           shell=True, capture_output=True, check=True).stdout
    assert json.loads(plain)["postgresql"] == here
    newer = remade(appliance, backup, lambda d, m: {**m, "postgresql": here + 2})
    psql("INSERT INTO chunks VALUES (3, 'Fictional passage three', '[7,8,9]')", gw_db)
    r = tool(env, "check", newer, "--identity", appliance["key"])
    assert r.returncode == 1 and f"made with PostgreSQL {here + 2}, newer than this appliance's {here}" in r.stdout, r.stdout
    r = tool(env, "restore", newer, "--identity", appliance["key"], "--yes")
    assert r.returncode == 1 and "restore failed (version)" in r.stdout, r.stdout
    assert psql("SELECT count(*) FROM chunks", gw_db) == "3"
    # Made before #57 (no version: PostgreSQL 16), or with an older version: restored.
    older = remade(appliance, backup, lambda d, m: {k: v for k, v in m.items() if k != "postgresql"})
    r = tool(env, "restore", older, "--identity", appliance["key"], "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert psql("SELECT count(*) FROM chunks", gw_db) == "2"


@needs
@pytest.mark.parametrize("change", [
    lambda d, m: ((d / "evil.sh").write_text("#!/bin/sh\n"), m)[1],                     # something else in the archive
    lambda d, m: ((d / "files" / "identity").mkdir(), m)[1],                            # a folder that isn't backed up
    lambda d, m: ((d / "db" / "other.dump").write_text("x"), m)[1],                     # another database's dump
    lambda d, m: (os.replace(d / "db" / f"{next(iter(m['databases']))}.dump", d / "dump"),
                  os.symlink("../dump", d / "db" / f"{next(iter(m['databases']))}.dump"), m)[2],  # a dump that is a link
])
def test_an_archive_holding_anything_else_is_refused(appliance, change):
    crafted = remade(appliance, first_backup(appliance), change)
    r = tool(appliance["env"], "restore", crafted, "--identity", appliance["key"], "--yes")
    assert r.returncode == 1 and "restore failed (read)" in r.stdout and "unexpected entry" in r.stdout, r.stdout


@needs
def test_the_databases_are_swapped_all_at_once_or_not_at_all(appliance):
    """Finding 8: the live databases were replaced one after the other, so a failure after the first left a
    mix. Now one transaction renames them all; here it fails after the first rename."""
    env, var, (gw_db, kc_db) = appliance["env"], appliance["var"], appliance["dbs"]
    backup = first_backup(appliance)
    psql("INSERT INTO chunks VALUES (3, 'Fictional passage three', '[7,8,9]')", gw_db)
    (var / "etc" / "gateway.env").write_text("changed after the backup\n")
    r = tool({**env, "SUVERYN_BACKUP_TEST_FAIL_SWAP": "1"}, "restore", backup, "--identity", appliance["key"], "--yes")
    assert r.returncode == 1 and "swapping in the restored databases failed; nothing on this appliance was changed" in r.stdout, r.stdout
    assert psql("SELECT count(*) FROM chunks", gw_db) == "3"  # the first database wasn't swapped either
    assert psql("SELECT count(*) FROM pg_database WHERE datname LIKE '%\\_restoring' OR datname LIKE '%\\_replaced'") == "0"
    assert (var / "etc" / "gateway.env").read_text() == "changed after the backup\n"  # nor the folders
    assert not list(var.glob("*.restoring"))
    # A session left open on a live database doesn't stop the swap (as dropdb --force didn't).
    holder = subprocess.Popen(["psql", "-qAt", "-h", PG_HOST, "-p", PG_PORT, "-U", "postgres", "-d", kc_db, "-c", "SELECT pg_sleep(60)"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = tool(env, "restore", backup, "--identity", appliance["key"], "--yes")
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 0, r.stdout + r.stderr
    assert psql("SELECT count(*) FROM chunks", gw_db) == "2" and "gw-pass" in (var / "etc" / "gateway.env").read_text()
    assert not list(var.glob("*.replaced")) and not list(var.glob("*.restoring"))


@needs
@pytest.mark.parametrize("fail, says", [
    ({"SUVERYN_BACKUP_TEST_FAIL_FOLDER": "etc"}, "putting the restored etc folder in place failed"),    # the first folder
    ({"SUVERYN_BACKUP_TEST_FAIL_FOLDER": "host"}, "putting the restored host folder in place failed"),  # after etc was swapped
    ({"SUVERYN_BACKUP_TEST_FAIL_ROLES": "1"}, "setting the roles' passwords failed"),                   # every folder in place
])
def test_a_failure_after_the_databases_were_swapped_puts_everything_back(appliance, fail, says):
    """Audit of 25 October 2026, finding 2: the folders were replaced after the databases' transaction had
    committed, so a failure there left restored databases beside old settings (and an unexpected error
    left the services stopped). Now the old folders and databases are kept until every step has worked."""
    env, var, (gw_db, kc_db), (gw_role, _) = appliance["env"], appliance["var"], appliance["dbs"], appliance["roles"]
    backup = first_backup(appliance)
    psql("INSERT INTO chunks VALUES (3, 'Fictional passage three', '[7,8,9]')", gw_db)
    (var / "etc" / "gateway.env").write_text("changed after the backup\n")
    (var / "host" / "hostname").write_text("renamed-after-the-backup\n")
    password = psql(f"SELECT rolpassword FROM pg_authid WHERE rolname = '{gw_role}'")
    r = tool({**env, **fail}, "restore", backup, "--identity", appliance["key"], "--yes")
    assert r.returncode == 1 and says in r.stdout and r.stdout.rstrip().endswith("the appliance was put back as it was"), r.stdout
    assert psql("SELECT count(*) FROM chunks", gw_db) == "3" and psql("SELECT count(*) FROM user_entity", kc_db) == "1"
    assert (var / "etc" / "gateway.env").read_text() == "changed after the backup\n"
    assert (var / "host" / "hostname").read_text() == "renamed-after-the-backup\n"
    assert psql(f"SELECT rolpassword FROM pg_authid WHERE rolname = '{gw_role}'") == password
    names = ", ".join(f"'{db}{s}'" for db in (gw_db, kc_db) for s in ("_restoring", "_replaced"))
    assert psql(f"SELECT count(*) FROM pg_database WHERE datname IN ({names})") == "0"
    assert not list(var.glob("*.restoring")) and not list(var.glob("*.replaced"))
    # and a restore without the failure still works afterwards
    assert tool(env, "restore", backup, "--identity", appliance["key"], "--yes").returncode == 0
    assert psql("SELECT count(*) FROM chunks", gw_db) == "2" and "gw-pass" in (var / "etc" / "gateway.env").read_text()


class Zeros:
    """A file of ``size`` zero bytes that is never written anywhere: a backup that packs small."""

    def __init__(self, size):
        self.left = size

    def read(self, n=-1):
        n = self.left if n < 0 else min(n, self.left)
        self.left -= n
        return b"\0" * n


def crafted(appliance, entries) -> Path:
    """A backup made by hand for the office's public key: the first backup's manifest and dumps, then
    ``entries`` (name, size) of zeros, streamed through zstd and age."""
    import tarfile
    good = first_backup(appliance)
    plain = subprocess.run(f'age -d -i "{appliance["key"]}" "{good}" | zstd -q -d -c', shell=True, capture_output=True, check=True).stdout
    recipient = json.loads(appliance["config"].read_text())["recipient"]
    out = appliance["target"] / f"crafted-{uuid.uuid4().hex[:6]}.tar.zst.age"
    with open(out, "wb") as f:
        age = subprocess.Popen(["age", "-r", recipient], stdin=subprocess.PIPE, stdout=f)
        zstd = subprocess.Popen(["zstd", "-q", "-c"], stdin=subprocess.PIPE, stdout=age.stdin)
        age.stdin.close()
        with tarfile.open(fileobj=zstd.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            import io
            with tarfile.open(fileobj=io.BytesIO(plain)) as src:
                for m in src.getmembers():
                    tar.addfile(m, src.extractfile(m) if m.isfile() else None)
            for name, size in entries:
                info = tarfile.TarInfo(name)
                info.size = size
                tar.addfile(info, Zeros(size))
        zstd.stdin.close()
        assert zstd.wait() == 0 and age.wait() == 0
    return out


@needs
@pytest.mark.parametrize("entries, extra_env, says", [
    ([(f"files/etc/n{i}", 0) for i in range(10_001)], {}, "more than 10000 entries"),
    ([("files/etc/big", 257 * 2**20)], {}, "folders are larger than 256 MiB"),
    ([], {"SUVERYN_BACKUP_TEST_UNPACK_BUDGET": "1000"}, "more than this appliance has room for"),
])
def test_a_backup_that_unpacks_to_too_much_is_refused_before_it_fills_the_disk(appliance, entries, extra_env, says):
    """Audit of 25 October 2026, finding 3: anyone with the public key can make a backup that packs small
    and unpacks huge. Unpacking stops at the bounds, nothing is changed, and nothing is left behind."""
    env, var, gw_db = appliance["env"], appliance["var"], appliance["dbs"][0]
    backup = crafted(appliance, entries)
    assert backup.stat().st_size < 4 * 2**20  # it packs small
    psql("INSERT INTO chunks VALUES (3, 'Fictional passage three', '[7,8,9]')", gw_db)
    env = {**env, **extra_env}
    r = tool(env, "check", backup, "--identity", appliance["key"])
    assert r.returncode == 1 and says in r.stdout, r.stdout
    r = tool(env, "restore", backup, "--identity", appliance["key"], "--yes")
    assert r.returncode == 1 and "restore failed (read)" in r.stdout and says in r.stdout, r.stdout
    assert psql("SELECT count(*) FROM chunks", gw_db) == "3"
    assert not [p for p in (var / "backup").iterdir() if p.name.startswith(("restore-", "check-"))]


@needs
def test_with_several_appliances_on_one_target_the_key_says_whose_backups_to_restore(appliance, tmp_path):
    """An office with two appliances backing up to the same share: the replacement for the first must restore
    the first's backups, even when the second's are newer. Each appliance has its own recovery key, and a
    backup is only offered when the key given opens it."""
    env, var = appliance["env"], appliance["var"]
    assert tool(env, "run").returncode == 0  # test-appliance, the office's recovery key
    other_key = tmp_path / "other.key"
    subprocess.run(["age-keygen", "-o", str(other_key)], capture_output=True, check=True)
    other = next(line.split(": ")[1] for line in other_key.read_text().splitlines() if line.startswith("# public key"))
    config = json.loads(appliance["config"].read_text())
    other_config = tmp_path / "other.json"
    other_config.write_text(json.dumps({**config, "recipient": other}))
    (var / "host" / "hostname").write_text("notary-02\n")
    import time
    time.sleep(1.1)  # a newer backup, by the second
    assert tool({**env, "SUVERYN_BACKUP_CONFIG": str(other_config)}, "run").returncode == 0
    (var / "host" / "hostname").write_text("suveryn-4f2a\n")  # the replacement for test-appliance
    given = tmp_path / "restore-settings.json"
    given.write_text(json.dumps({"target": "folder", "path": str(appliance["target"])}))
    env = {**env, "SUVERYN_BACKUP_CONFIG": str(tmp_path / "none.json")}

    r = tool(env, "--settings", given, "find", "--json", "--identity", appliance["key"])
    found = json.loads(r.stdout)
    assert r.returncode == 0 and [b["appliance"] for b in found["backups"]] == ["test-appliance"], r.stdout
    r = tool(env, "--settings", given, "find", "--json", "--identity", other_key)
    assert [b["appliance"] for b in json.loads(r.stdout)["backups"]] == ["notary-02"]
    r = tool(env, "--settings", given, "check", "newest", "--on-target", "--json", "--identity", appliance["key"])
    assert json.loads(r.stdout)["appliance"] == "test-appliance", r.stdout  # not the newer notary-02
    # notary-02's backup by name, with test-appliance's key: not offered
    r = tool(env, "--settings", given, "check", json.loads(tool(env, "--settings", given, "find", "--json", "--identity",
             other_key).stdout)["backups"][0]["file"], "--on-target", "--json", "--identity", appliance["key"])
    assert r.returncode == 1 and json.loads(r.stdout)["step"] == "target"
    # a key that opens none of them
    stranger = tmp_path / "stranger.key"
    subprocess.run(["age-keygen", "-o", str(stranger)], capture_output=True, check=True)
    r = tool(env, "--settings", given, "find", "--json", "--identity", stranger)
    assert r.returncode == 1 and json.loads(r.stdout)["step"] == "decrypt"
    r = tool(env, "--settings", given, "check", "newest", "--on-target", "--json", "--identity", stranger)
    assert r.returncode == 1 and json.loads(r.stdout)["step"] == "decrypt"


@needs
def test_a_full_disk_clears_what_is_past_the_retention_first_and_then_says_it_is_full(appliance):
    """Review of the backup strategy: backups past the retention period went only after a success, so a full
    disk failed every night for good. Now they go first; if it still doesn't fit, the step is "space"."""
    env, folder = appliance["env"], appliance["target"] / "suveryn-backups" / "test-appliance"
    assert tool(env, "run").returncode == 0
    newest = next(folder.glob("*.age")).name
    old = folder / "suveryn-backup_test-appliance_20200101T000000Z.tar.zst.age"
    old.write_bytes(b"an old backup")
    (folder / f"{old.name}.sha256").write_text("0  x\n")
    # the newest stays the newest: the old one is past the 30 days
    r = tool({**env, "SUVERYN_BACKUP_TEST_FREE": "1000"}, "run")
    assert r.returncode == 1 and "backup failed (space): the backup disk is full" in r.stdout, r.stdout
    assert not old.exists() and not (folder / f"{old.name}.sha256").exists()  # cleared before the check
    assert sorted(p.name for p in folder.iterdir()) == [newest, f"{newest}.sha256"]  # the newest always stays
    status = json.loads((appliance["var"] / "backup" / "status.json").read_text())
    assert status["last_error"]["step"] == "space"


@needs
def test_each_backup_is_read_back_from_the_disk_and_its_checksum_kept_and_compared(appliance):
    """Review of the backup strategy: a backup was checked only by its size. Now it is read back from the
    disk and compared, its checksum stays beside it, and check and restore compare it first."""
    env, folder = appliance["env"], appliance["target"] / "suveryn-backups" / "test-appliance"
    r = tool({**env, "SUVERYN_BACKUP_TEST_CORRUPT": "1"}, "run")
    assert r.returncode == 1 and "backup failed (verify): the backup disk didn't give back what was written" in r.stdout, r.stdout
    assert not list(folder.iterdir())  # nothing half-written left
    assert tool(env, "run").returncode == 0
    [backup] = folder.glob("*.age")
    import hashlib
    sidecar = (folder / f"{backup.name}.sha256").read_text().split()
    assert sidecar == [hashlib.sha256(backup.read_bytes()).hexdigest(), backup.name]
    status = json.loads((appliance["var"] / "backup" / "status.json").read_text())
    assert status["last_success"]["sha256"] == sidecar[0]
    with open(backup, "r+b") as f:  # the disk changes a byte later on
        f.seek(200)
        b = f.read(1)
        f.seek(200)
        f.write(bytes([b[0] ^ 1]))
    r = tool(env, "check", backup, "--identity", appliance["key"])
    assert r.returncode == 1 and "its checksum doesn't match; the disk may be failing" in r.stdout, r.stdout


def test_a_second_usb_disk_for_rotation_is_recognised_and_another_offices_never(tmp_path):
    """Review of the backup strategy: only the one disk chosen in the setup was accepted, so two disks swapped
    weekly (one kept elsewhere) weren't possible. A disk with a suveryn-backups folder is this office's,
    unless the folder is marked with another office's key."""
    m = load()
    listing = {"blockdevices": [
        {"name": "sda", "path": "/dev/sda", "tran": "sata", "type": "disk", "fstype": None, "children": [
            {"name": "sda1", "path": "/dev/sda1", "type": "part", "fstype": "ext4"}]},                      # the appliance's own
        {"name": "sdb", "path": "/dev/sdb", "tran": "usb", "type": "disk", "model": "Extreme  SSD", "serial": "4C53", "children": [
            {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "exfat"},
            {"name": "sdb2", "path": "/dev/sdb2", "type": "part", "fstype": "swap"}]},
        {"name": "sdc", "path": "/dev/sdc", "tran": "usb", "type": "disk", "model": "Elements", "serial": None, "fstype": "ntfs"}]}
    assert m.usb_candidates(listing) == [(Path("/dev/sdb1"), "usb-Extreme_SSD_4C53"), (Path("/dev/sdc"), "usb-Elements_sdc")]
    ours, theirs = "age1" + "q" * 58, "age1" + "p" * 58
    top = tmp_path / "suveryn-backups"
    assert not m.office_disk(top, ours)            # no folder: someone's own disk, never written to
    top.mkdir()
    assert m.office_disk(top, ours)                # the office made the folder on its second disk
    (top / m.OFFICE_MARK).write_text(ours + "\n")
    assert m.office_disk(top, ours)
    (top / m.OFFICE_MARK).write_text(theirs + "\n")
    assert not m.office_disk(top, ours)            # another office's (or another appliance's) disk


@needs
def test_the_administrators_key_test_opens_the_newest_backup_and_deletes_the_key(appliance, tmp_path):
    """Review of the backup strategy: the appliance can't read its own backups, so a lost key or a damaged
    backup showed only on the day of a restore. The administrator's test checks the newest with the key,
    restores nothing, and deletes the key whatever happens."""
    drop = tmp_path / "key-test"
    drop.mkdir()
    env = {**appliance["env"], "SUVERYN_BACKUP_KEY_TEST": str(drop)}
    result = appliance["var"] / "backup" / "key-test.json"

    def test_with(key_text):
        (drop / "key").write_text(key_text)
        (drop / "request").write_text("asked\n")
        r = tool(env, "key-test")
        assert not (drop / "key").exists() and not (drop / "request").exists()  # gone, whatever happened
        return r, json.loads(result.read_text())

    r, out = test_with(appliance["key"].read_text())
    assert r.returncode == 1 and out["ok"] is False and out["step"] == "target"  # nothing backed up yet
    assert tool(env, "run").returncode == 0
    r, out = test_with(appliance["key"].read_text())
    assert r.returncode == 0 and out["ok"] is True and out["file"].startswith("suveryn-backup_test-appliance_"), out
    assert oct(result.stat().st_mode & 0o777) == "0o644" and "AGE-SECRET-KEY" not in result.read_text()
    other = tmp_path / "other.key"
    subprocess.run(["age-keygen", "-o", str(other)], capture_output=True, check=True)
    r, out = test_with(other.read_text())  # another appliance's key, or an old one
    assert r.returncode == 1 and out["ok"] is False and out["step"] == "decrypt" and "isn't this appliance's recovery key" in out["message"]
    assert tool(env, "key-test").returncode == 1 and json.loads(result.read_text())["step"] == "settings"  # no key given


@needs
@pytest.mark.skipif(os.geteuid() != 0 or not shutil.which("runuser"), reason="needs root and runuser, as on the appliance")
def test_check_and_the_key_test_read_the_dumps_as_postgresql_without_opening_them_to_everyone(appliance, tmp_path):
    """Pod check of 27 October 2026, finding 1: pg_restore runs as PostgreSQL's superuser, which couldn't open
    root's 0700 work folder, so ``check`` and the key test called every good backup damaged. Run as on the
    appliance: root, with pg_restore through runuser. The work folder sits where postgres may pass (the
    appliance's /var/lib/suveryn/backup is 0755; pytest's own folders are root's 0700)."""
    import pwd
    try:
        pwd.getpwnam("postgres")
    except KeyError:
        pytest.skip("no postgres user")
    state = Path(tempfile.mkdtemp(prefix="suveryn-backup-state-", dir="/var/tmp"))
    try:
        os.chmod(state, 0o755)
        env = {**appliance["env"], "SUVERYN_BACKUP_STATE": str(state)}
        assert tool(env, "run").returncode == 0
        [backup] = (appliance["target"] / "suveryn-backups" / "test-appliance").glob("*.age")
        as_appliance = {**env, "SUVERYN_BACKUP_PG_USER": "postgres"}
        r = tool(as_appliance, "check", backup, "--identity", appliance["key"])
        assert r.returncode == 0 and r.stdout.startswith("usable:"), r.stdout + r.stderr
        drop = tmp_path / "key-test"
        drop.mkdir()
        (drop / "key").write_text(appliance["key"].read_text())
        r = tool({**as_appliance, "SUVERYN_BACKUP_KEY_TEST": str(drop)}, "key-test")
        assert r.returncode == 0, r.stdout + r.stderr
        assert json.loads((state / "key-test.json").read_text())["ok"] is True
        r = tool(as_appliance, "restore", backup, "--identity", appliance["key"], "--yes")  # pg_restore into the databases too
        assert r.returncode == 0, r.stdout + r.stderr
        assert not [p for p in state.iterdir() if p.name.startswith("check-")]  # nothing left behind
    finally:
        shutil.rmtree(state, ignore_errors=True)


def test_the_unpacked_dumps_are_postgresqls_alone(tmp_path, monkeypatch):
    """The work folder: root:postgres 0710; the dumps postgres's own, 0600 in a 0700 folder. Never 0711 with
    readable files, which anyone who lists the backup's state folder could read."""
    if os.geteuid() != 0:
        pytest.skip("needs root")
    import pwd
    try:
        pg = pwd.getpwnam("postgres")
    except KeyError:
        pytest.skip("no postgres user")
    b = load()
    monkeypatch.setattr(b, "PG_USER", "postgres")
    (tmp_path / "work" / "db").mkdir(parents=True)
    (tmp_path / "work" / "db" / "suveryn.dump").write_text("fictional")
    os.chmod(tmp_path / "work" / "db" / "suveryn.dump", 0o644)
    b.hand_dumps_to_postgres(tmp_path / "work")
    work, db, dump = tmp_path / "work", tmp_path / "work" / "db", tmp_path / "work" / "db" / "suveryn.dump"
    assert (work.stat().st_uid, work.stat().st_gid, work.stat().st_mode & 0o777) == (0, pg.pw_gid, 0o710)
    assert (db.stat().st_uid, db.stat().st_mode & 0o777) == (pg.pw_uid, 0o700)
    assert (dump.stat().st_uid, dump.stat().st_mode & 0o777) == (pg.pw_uid, 0o600)
