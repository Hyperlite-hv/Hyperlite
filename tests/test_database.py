"""Schema creation and migrations."""

from app.core import database


def test_init_db_is_idempotent(database):
    database.init_db()
    database.init_db()  # running the migrations again on an existing database must not fail


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
        "deployment_profile",
        "allocation_policy",
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
        "hashed_password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'observateur')"
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
            "INSERT INTO tasks (id, type, statut, cree_le, node) VALUES ('t1', 'x', 'termine', 'now', 'kvm-lab')"
        )
        conn.commit()
    init_db()
    with get_conn() as conn:
        assert conn.execute("SELECT node FROM tasks WHERE id = 't1'").fetchone()[0] == "local"
