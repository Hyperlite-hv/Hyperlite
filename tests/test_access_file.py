"""The permission tables in a file of their own (app/core/database.py, ACCESS; docs/design/privilege-separation.md,
section 14.3): written by root's services only, read-only for the API, moved there once from the main database, and
back on a return to an older version."""

import sqlite3

import pytest

from app.core import config_copy


def tables(path):
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    finally:
        conn.close()


def as_one_file(database):
    """The database as a version from before the access file left it: the permission tables in the main file."""
    conn = sqlite3.connect(database.DB_PATH)
    config_copy._add_access_tables(conn)
    conn.close()
    database.access_path().unlink()


def test_the_permission_tables_are_in_their_own_file_written_by_root(database):
    access = database.access_path()
    assert set(database.ACCESS_TABLES) <= tables(access)
    assert not set(database.ACCESS_TABLES) & tables(database.DB_PATH)
    assert oct(access.stat().st_mode & 0o777) == "0o640"  # root writes it, the API's group reads it (section 14.6)


def test_a_node_from_before_moves_them_once_with_their_ids_and_counters(database):
    with database.get_conn() as db:
        db.execute("INSERT INTO groups (id, name) VALUES (40, 'ops')")
        db.execute("DELETE FROM groups WHERE id = 40")  # the counter stays at 40
        db.execute("INSERT INTO groups (id, name) VALUES (7, 'dev')")
        db.commit()
    as_one_file(database)
    assert "groups" in tables(database.DB_PATH)
    database.init_db()
    assert "groups" not in tables(database.DB_PATH)
    with database.get_conn() as db:
        assert [tuple(r) for r in db.execute("SELECT id, name FROM groups")] == [(7, "dev")]
        db.execute("INSERT INTO groups (name) VALUES ('qa')")
        assert db.execute("SELECT id FROM groups WHERE name = 'qa'").fetchone()[0] == 41
    database.init_db()  # nothing left to move


def test_a_return_to_an_older_version_finds_the_permission_tables_in_the_database(database):
    """installer/deb/prerm, on a downgrade: the older version reads the main file, and the next newer start moves them
    again, with what the older version wrote."""
    with database.get_conn() as db:
        db.execute("INSERT INTO groups (name) VALUES ('ops')")
        db.commit()
    assert database.move_back("before-1.3.1")
    access = database.access_path()
    assert not access.exists() and access.with_name(access.name + ".before-1.3.1").exists()
    older = sqlite3.connect(database.DB_PATH)
    assert older.execute("SELECT name FROM groups").fetchall() == [("ops",)]
    older.execute("INSERT INTO groups (name) VALUES ('written-by-the-older-version')")
    older.commit()
    older.close()
    assert not database.move_back("again")  # nothing left to move
    database.init_db()
    with database.get_conn() as db:
        assert [r[0] for r in db.execute("SELECT name FROM groups ORDER BY id")] == [
            "ops",
            "written-by-the-older-version",
        ]
    assert "groups" not in tables(database.DB_PATH)


@pytest.mark.parametrize(
    "shadow",
    [
        "CREATE VIEW acl AS SELECT 1 AS id, 'user' AS subject_type, 'eve' AS subject_id, 'admin' AS role, "
        "'vm' AS resource_type, 'web' AS resource_id",
        "CREATE TABLE acl (id INTEGER PRIMARY KEY, subject_type TEXT, subject_id TEXT, role TEXT, resource_type TEXT, "
        "resource_id TEXT)",
    ],
)
def test_a_permission_table_made_in_the_main_file_is_never_read_in_its_place(database, shadow):
    """The API (www-data from 4e) writes the main file, where unqualified names are looked up first."""
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO acl (subject_type, subject_id, role, resource_type, resource_id) VALUES "
            "('user', 'ana', 'viewer', 'vm', 'web')"
        )
        db.commit()
    planted = sqlite3.connect(database.DB_PATH)
    planted.execute(shadow)
    if shadow.startswith("CREATE TABLE"):
        planted.execute("INSERT INTO acl VALUES (1, 'user', 'eve', 'admin', 'vm', 'web')")
    planted.commit()
    planted.close()
    with database.get_conn() as db, pytest.raises(sqlite3.DatabaseError, match=r"prohibited|not authorized"):
        db.execute("SELECT subject_id, role FROM acl").fetchall()
    database.init_db()  # the next start sets it aside
    with database.get_conn() as db:
        assert [tuple(r) for r in db.execute("SELECT subject_id, role FROM acl")] == [("ana", "viewer")]


def test_a_process_that_may_not_write_the_file_reads_it_only(database, monkeypatch):
    monkeypatch.setattr(database, "access_writable", lambda path=None: False)
    with database.get_conn() as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("INSERT INTO groups (name) VALUES ('ops')")


def test_the_cluster_copy_bundle_holds_them(database, tmp_path):
    with database.get_conn() as db:
        db.execute("INSERT INTO groups (name) VALUES ('ops')")
        db.commit()
    bundle, meta = config_copy.build_bundle(tmp_path)
    work = tmp_path / "x"
    import tarfile

    with tarfile.open(bundle) as tar:
        tar.extractall(work, filter="data")
    copy = sqlite3.connect(work / "hyperlite.db")
    assert copy.execute("SELECT name FROM groups").fetchall() == [("ops",)]
    copy.close()
    assert meta["fingerprint"] == config_copy.config_digest()
