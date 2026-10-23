"""Schema creation and migrations."""

from app.core import database


def test_init_db_is_idempotent(database):
    def schema():
        with database.get_conn() as conn:
            return sorted(tuple(r) for r in conn.execute("SELECT type, name, sql FROM sqlite_master"))

    with database.get_conn() as conn:
        conn.execute("INSERT INTO users (username, hashed_password, role) VALUES ('keep', 'x', 'admin')")
        conn.commit()
    before = schema()
    database.init_db()
    database.init_db()  # running the migrations again on an existing database must not fail...
    assert schema() == before  # ...nor change the schema...
    with database.get_conn() as conn:  # ...nor touch the data
        assert conn.execute("SELECT role FROM users WHERE username = 'keep'").fetchone()[0] == "admin"


def test_core_tables_exist(database):
    with database.get_conn() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "users",
        "audit_log",
        "tasks",
        "acl",
        "groups",
        "nodes",
        "backups",
        "api_tokens",
        "sso_config",
        "notification_channels",
        "ha_protected_vms",
    }
    assert expected <= tables


def test_connections_use_wal_mode(database):
    with database.get_conn() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_upgrade_adds_columns_missing_from_an_older_schema(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, "
        "hashed_password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'viewer')"
    )
    old.commit()
    old.close()
    monkeypatch.setattr(database, "DB_PATH", path)
    database.init_db()
    with database.get_conn() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    assert "auth_source" in columns


def test_legacy_local_node_label_is_migrated(database):
    from app.core.database import get_conn, init_db

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (id, type, status, created_at, node) VALUES ('t1', 'x', 'succeeded', 'now', 'kvm-lab')"
        )
        conn.commit()
    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT node FROM tasks WHERE id = 't1'").fetchone()[0] == "local"


OLD_ACL = (
    "CREATE TABLE {name} (id INTEGER PRIMARY KEY AUTOINCREMENT, subject_type TEXT NOT NULL CHECK(subject_type IN "
    "('user','group')), subject_id TEXT NOT NULL, role TEXT NOT NULL, resource_type TEXT NOT NULL CHECK(resource_type "
    "IN ('vm','pool','container')), resource_id TEXT NOT NULL)"
)


def _old_database(tmp_path, monkeypatch, table="acl"):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute(OLD_ACL.format(name=table))
    row = {
        "acl": "INSERT INTO acl VALUES (7, 'user', 'ana', 'operator', 'vm', 'web')",
        "acl_pre_container_migration": "INSERT INTO acl_pre_container_migration VALUES (7, 'user', 'ana', 'operator', 'vm', 'web')",
    }
    old.execute(row[table])
    old.commit()
    old.close()
    monkeypatch.setattr(database, "DB_PATH", path)


def test_the_acl_rebuild_reserves_no_id_from_the_cluster(tmp_path, monkeypatch):
    """The copied rows keep their ids. In a cluster, reserving one failed while hyperlite-cfs agreed on its state
    after an update (1.3.0 on a production node): the service did not start, and the assignments were left aside."""
    from app.repositories.cfs import ids

    def refused():
        raise ids.NoId("hyperlite-cfs does not answer: no new row of acl can be created")

    _old_database(tmp_path, monkeypatch)
    monkeypatch.setattr(ids, "_in_cluster", lambda: True)
    monkeypatch.setattr(ids, "reserve", refused)
    database.init_db()
    with database.get_conn() as conn:
        assert [tuple(r) for r in conn.execute("SELECT id, subject_id, resource_id FROM acl")] == [(7, "ana", "web")]


def test_a_rebuild_that_stopped_half_way_is_finished(tmp_path, monkeypatch):
    """The new acl table made, the copy not done: the next start copies the assignments and drops the old table."""
    _old_database(tmp_path, monkeypatch, table="acl_pre_container_migration")
    database.init_db()
    with database.get_conn() as conn:
        assert [tuple(r) for r in conn.execute("SELECT id, subject_id FROM acl")] == [(7, "ana")]
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "acl_pre_container_migration" not in names
