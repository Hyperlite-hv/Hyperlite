"""API v1, the database in English (app/core/schema_v1.py, docs/design/api-v1.md): a database of an older version is
renamed at start, once, after a copy of it is kept; rows of the replicated configuration written before the renames
are read with the new names."""

import json
import sqlite3
from pathlib import Path

import pytest

from app.core import schema_v1


def as_before_v1(path, monkeypatch):
    """The database as an older version left it: the migration run backwards (every renamed column and value under its
    old name, the CHECKs naming the old values), with rows."""
    columns = {t: {new: old for old, new in r.items()} for t, r in schema_v1.COLUMNS.items()}
    values = {}
    for (table, column), renames in schema_v1.VALUES.items():
        old_column = {new: old for old, new in schema_v1.COLUMNS.get(table, {}).items()}.get(column, column)
        values[(table, old_column)] = {new: old for old, new in renames.items()}
    from app.core import config_copy, database

    # One file, as before the permission tables had their own (database.ACCESS).
    conn = sqlite3.connect(path)
    config_copy._add_access_tables(conn)
    conn.close()
    database.access_path().unlink()
    with monkeypatch.context() as m:
        m.setattr(schema_v1, "COLUMNS", columns)
        m.setattr(schema_v1, "VALUES", values)
        conn = sqlite3.connect(path)
        schema_v1.migrate(conn, path)
        conn.close()
    Path(f"{path}{schema_v1.BACKUP_SUFFIX}").unlink()  # that copy was of the new database
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO webauthn_credentials (username, nom, credential_id, public_key, rp_id, cree_le, utilise_le) "
        "VALUES ('ana', 'YubiKey', 'c1', 'pk', 'hl.lan', '2026-10-01T00:00:00+00:00', NULL)"
    )
    conn.execute("INSERT INTO users (username, hashed_password, role) VALUES ('bob', 'x', 'observateur')")
    conn.execute(
        "INSERT INTO tasks (id, type, cible, statut, progres, cree_le) VALUES ('t1', 'backup', 'web', 'en_cours', 0, 'now')"
    )
    conn.execute(
        "INSERT INTO nodes (name, hostname, statut, added_at) VALUES ('pve-a', 'pve-a.lan', 'hors_ligne', 'now')"
    )
    conn.commit()
    conn.close()


def columns(path, table):
    from app.core import database

    if table in database.ACCESS_TABLES and path == database.DB_PATH and database.access_path().exists():
        path = database.access_path()  # moved there once renamed
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    finally:
        conn.close()


def test_an_older_database_is_renamed_once_after_a_copy(database, monkeypatch):
    path = database.DB_PATH
    as_before_v1(path, monkeypatch)
    database.init_db()
    assert {"name", "created_at", "used_at"} <= columns(path, "webauthn_credentials")
    assert "nom" not in columns(path, "webauthn_credentials")
    with database.get_conn() as db:
        assert dict(db.execute("SELECT name, created_at FROM webauthn_credentials").fetchone()) == {
            "name": "YubiKey",
            "created_at": "2026-10-01T00:00:00+00:00",
        }
    with database.get_conn() as db:
        assert db.execute("SELECT role FROM users WHERE username = 'bob'").fetchone()[0] == "viewer"
        assert db.execute("SELECT status FROM tasks").fetchone()[0] == "running"
        assert db.execute("SELECT status FROM nodes").fetchone()[0] == "offline"
        db.execute("UPDATE tasks SET status = 'succeeded'")  # the CHECK names the new values
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE tasks SET status = 'termine'")
        assert db.execute(f"SELECT seq FROM {database.ACCESS}.sqlite_sequence WHERE name = 'users'").fetchone()[0] >= 1  # noqa: S608
    copy = path.with_name(path.name + schema_v1.BACKUP_SUFFIX)
    assert "nom" in columns(copy, "webauthn_credentials")  # the database as it was
    assert oct(copy.stat().st_mode & 0o777) == "0o600"
    before = copy.stat().st_mtime_ns
    database.init_db()  # already renamed: nothing to do, the copy stays the one from before
    assert copy.stat().st_mtime_ns == before
    with database.get_conn() as db:
        assert schema_v1.pending(db) == []


def test_a_rename_that_fails_leaves_the_database_as_it_was(database, monkeypatch):
    path = database.DB_PATH
    as_before_v1(path, monkeypatch)
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO groups (name) VALUES (?)", [("a",), ("b",)])
    values = schema_v1.VALUES
    # A value renamed onto one that exists (groups.name is UNIQUE): the run fails after it renamed and rebuilt the rest.
    monkeypatch.setattr(schema_v1, "VALUES", {**values, ("groups", "name"): {"a": "b"}})
    with pytest.raises(sqlite3.IntegrityError):
        database.init_db()
    assert "nom" in columns(path, "webauthn_credentials")  # nothing of that run was kept
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT statut FROM tasks").fetchone()[0] == "en_cours"
    monkeypatch.setattr(schema_v1, "VALUES", values)
    database.init_db()  # the next start does it all
    assert "name" in columns(path, "webauthn_credentials") and "expires_at" in columns(path, "webauthn_challenges")


def test_a_fresh_database_has_only_the_new_names(database):
    with database.get_conn() as db:
        assert schema_v1.pending(db) == []
    assert not database.DB_PATH.with_name(database.DB_PATH.name + schema_v1.BACKUP_SUFFIX).exists()


def test_stored_values_are_renamed_too(database, monkeypatch):
    monkeypatch.setattr(schema_v1, "VALUES", {("webauthn_challenges", "purpose"): {"connexion": "login"}})
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO webauthn_challenges (username, purpose, challenge, rp_id, origin, expires_at) "
            "VALUES ('ana', 'connexion', 'x', 'hl.lan', 'https://hl.lan', 'later')"
        )
        db.commit()
    database.init_db()
    with database.get_conn() as db:
        assert db.execute("SELECT purpose FROM webauthn_challenges").fetchone()[0] == "login"


def test_a_row_of_the_tree_written_before_the_renames_reads_with_the_new_names(monkeypatch):
    monkeypatch.setattr(schema_v1, "VALUES", {("webauthn_challenges", "purpose"): {"connexion": "login"}})
    old = {"id": 3, "nom": "YubiKey", "cree_le": "t", "username": "ana"}
    assert schema_v1.row("webauthn_credentials", old) == {
        "id": 3,
        "name": "YubiKey",
        "created_at": "t",
        "username": "ana",
    }
    assert schema_v1.row("webauthn_challenges", {"but": "connexion"}) == {"purpose": "login"}
    assert schema_v1.row("groups", {"id": 1, "name": "ops"}) == {"id": 1, "name": "ops"}


def test_another_nodes_row_with_the_old_names_is_applied_and_then_equal(database, monkeypatch):
    from app.core import cluster_lead
    from app.repositories.cfs import ids, inbound, shadow
    from tests.test_cfs_inbound import Cluster

    fake = Cluster()
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    monkeypatch.setattr(inbound, "state", inbound._State())
    monkeypatch.setattr(cluster_lead, "STATUS_TTL_S", 0)
    cluster_lead.forget()
    ids.forget()
    shadow.seed()
    fake.remote("/db/groups/1", {"id": 1, "name": "ops"})  # the tree is never empty in a cluster (a safeguard)
    row = {"id": 7, "username": "ana", "nom": "YubiKey", "credential_id": "c7", "public_key": "pk", "rp_id": "hl.lan",
           "cree_le": "2026-10-01T00:00:00+00:00"}  # fmt: skip
    fake.remote("/priv/db/webauthn_credentials/7", row)  # written by a node before the renames
    assert inbound.apply() == 2
    with database.get_conn() as db:
        assert db.execute("SELECT name FROM webauthn_credentials WHERE id = 7").fetchone()[0] == "YubiKey"
    inbound.reapply()
    assert inbound.apply() == 0  # read again: the same row as this node's, nothing written
    assert (
        json.loads(fake.files["/priv/db/webauthn_credentials/7"])["nom"] == "YubiKey"
    )  # the tree is not rewritten here


def test_a_backup_made_before_the_renames_reads_with_the_new_mode(tmp_path):
    from app.core import backup_integrity

    (tmp_path / backup_integrity.MANIFEST).write_text(json.dumps({"version": 1, "vm": "web", "mode": "chaud"}))
    assert backup_integrity.read_manifest(tmp_path)["mode"] == "live"
    assert json.loads((tmp_path / backup_integrity.MANIFEST).read_text())["mode"] == "chaud"  # never rewritten


def test_a_configuration_copy_made_before_the_renames_is_read_with_the_new_names(tmp_path):
    import tarfile

    from app.core import config_copy

    work = tmp_path / "old"
    work.mkdir()
    (work / "meta.json").write_text(json.dumps({"version": 1, "controleur": "pve-a", "cree_le": "t", "empreinte": "e"}))
    (work / "cles.env").write_text("HYPERLITE_SECRET_KEY=s\n")
    (work / "hyperlite.db").write_bytes(b"")
    bundle = tmp_path / "config-1.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        for name in ("hyperlite.db", "cles.env", "meta.json"):
            tar.add(work / name, arcname=name)
    meta, extracted = config_copy.read_bundle(bundle)
    assert meta == {"version": 1, "controller": "pve-a", "created_at": "t", "fingerprint": "e"}
    assert (extracted / "keys.env").read_text() == "HYPERLITE_SECRET_KEY=s\n"


def test_the_cluster_configuration_written_before_reads_with_the_new_names():
    from app.core import cluster_setup

    old = {"cluster": "labo", "version": 5, "membres": [
        {"nom": "hlc1", "nodeid": 1, "adresse": "10.10.0.11", "voix": 1},
        {"nom": "hlc2", "nodeid": 2, "adresse": "10.10.0.12", "voix": 2},
    ]}  # fmt: skip
    conf = cluster_setup._check_conf(old)  # lab, 2026-10-19: "The cluster's configuration is not valid"
    assert [(m["name"], m["address"], m["votes"]) for m in conf["members"]] == [
        ("hlc1", "10.10.0.11", 1),
        ("hlc2", "10.10.0.12", 2),
    ]
