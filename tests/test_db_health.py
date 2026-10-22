"""This node's database: the checks find each kind of damage, the repair that fits it is offered, the online repair
and the rebuild leave a sound file, and the background loops' repeated corruption errors are counted."""

import sqlite3

import pytest

from app.core import db_health


@pytest.fixture(autouse=True)
def _fresh_state():
    db_health.forget()
    yield
    db_health.forget()


@pytest.fixture()
def logged(monkeypatch):
    """What db_health records in the audit log (and so notifies): (username, action, result, detail)."""
    out = []
    monkeypatch.setattr(db_health, "_log", lambda *a: out.append(a))
    return out


def _sqlite_error(cls, code):
    """An error as SQLite raises it: its code set."""
    e = cls("as SQLite says it")
    e.sqlite_errorcode = code
    return e


def _malformed():
    return _sqlite_error(sqlite3.DatabaseError, 11)


def _locked():
    return _sqlite_error(sqlite3.OperationalError, 5)


def _page_size(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA page_size").fetchone()[0]


def _settle(path):
    """Everything written so far in the file itself, none of it left in the write-ahead log."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _root(path, name):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT rootpage FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]


def _read_page(path, page):
    size = _page_size(path)
    with open(path, "rb") as f:
        f.seek((page - 1) * size)
        return f.read(size)


def _write_page(path, page, data, offset=0):
    size = _page_size(path)
    with open(path, "r+b") as f:
        f.seek((page - 1) * size + offset)
        f.write(data)


def _table_with_index(path, rows=10):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, label TEXT)")
        conn.execute("CREATE INDEX sample_label ON sample (label)")
        conn.executemany("INSERT INTO sample (label) VALUES (?)", [(f"label-{i}",) for i in range(rows)])
    _settle(path)


def _stale_index(path):
    """Index entries that disagree with their table, on sound pages: an index page written back as it was before
    rows were added, as a disk that lost its last writes leaves it."""
    _table_with_index(path)
    page = _root(path, "sample_label")
    before = _read_page(path, page)
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO sample (label) VALUES (?)", [(f"later-{i}",) for i in range(10)])
    _settle(path)
    _write_page(path, page, before)


def _damaged_index_page(path):
    """An index page whose cell pointers are garbage: SQLite cannot even free it."""
    _table_with_index(path, rows=3000)
    _write_page(path, _root(path, "sample_label"), b"\xff" * 40, offset=12)


def test_corruption_errors_are_told_apart_from_the_others():
    assert db_health.is_corruption(_malformed())
    assert db_health.is_corruption(_sqlite_error(sqlite3.DatabaseError, 26))  # not a database
    assert db_health.is_corruption(_sqlite_error(sqlite3.DatabaseError, 11 | (1 << 8)))  # SQLITE_CORRUPT_VTAB
    assert not db_health.is_corruption(_locked())
    assert not db_health.is_corruption(_sqlite_error(sqlite3.IntegrityError, 2067))  # a UNIQUE constraint
    assert not db_health.is_corruption(sqlite3.DatabaseError("no code: not SQLite's"))
    try:
        try:
            raise _malformed()
        except sqlite3.DatabaseError as e:
            raise RuntimeError("tick failed") from e
    except RuntimeError as wrapped:
        assert db_health.is_corruption(wrapped)


def test_the_codes_sqlite_gives_are_read(tmp_path):
    path = tmp_path / "x.db"
    _damaged_index_page(path)
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError) as raised:
        conn.execute("REINDEX sample_label")
    conn.close()
    assert raised.value.sqlite_errorname == "SQLITE_CORRUPT"
    assert db_health.is_corruption(raised.value)


def test_a_sound_database_is_ok(database, logged):
    state = db_health.check()
    assert state["status"] == "ok" and state["repair"] is None and state["checked_at"]
    assert logged == []


def test_stale_index_entries_are_seen_by_the_full_check_only_and_reindexed_online(database, logged):
    _stale_index(database.DB_PATH)
    assert db_health.inspect()["ok"], "quick_check does not compare an index with its table"

    state = db_health.check(full=True)
    assert state["status"] == "damaged"
    assert state["kind"] == "index_entries" and state["indexes"] == ["sample_label"]
    assert state["repair"] == "reindex"
    assert logged[-1][1:3] == ("database_check", "failed")

    after = db_health.repair("admin")
    assert after["status"] == "ok"
    assert logged[-1][:3] == ("admin", "repair_database", "success")
    assert ("system", "database_check", "success") in [entry[:3] for entry in logged]
    with sqlite3.connect(database.DB_PATH) as conn:
        assert conn.execute("SELECT count(*) FROM sample INDEXED BY sample_label WHERE label > ''").fetchone()[0] == 20


def test_damaged_index_pages_need_a_rebuild_which_keeps_every_row(tmp_path, logged):
    path = tmp_path / "x.db"
    _damaged_index_page(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE ids (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
        conn.executemany("INSERT INTO ids (v) VALUES (?)", [("a",), ("b",)])
        conn.execute("DELETE FROM ids WHERE v = 'b'")
        conn.execute("PRAGMA user_version = 7")

    verdict = db_health.inspect(path)
    assert verdict["kind"] == "index_pages" and "sample_label" in verdict["indexes"]
    assert db_health.repair_for(verdict) == "rebuild"

    result = db_health.rebuild(path)
    assert result["rows"]["sample"] == 3000 and result["emptied"] == []
    assert db_health.inspect(path, full=True)["ok"]
    with sqlite3.connect(path) as conn:
        assert (
            conn.execute("SELECT count(*) FROM sample INDEXED BY sample_label WHERE label > ''").fetchone()[0] == 3000
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        # The id of a deleted row is not handed out again.
        conn.execute("INSERT INTO ids (v) VALUES ('c')")
        assert conn.execute("SELECT id FROM ids WHERE v = 'c'").fetchone()[0] == 3
    kept = tmp_path / result["kept"].rsplit("/", 1)[1]
    assert kept.exists() and not db_health.inspect(kept)["ok"], "the damaged file is kept as it was"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_the_online_repair_refuses_what_it_cannot_mend(database, client, auth_headers, monkeypatch):
    headers = auth_headers("root")
    answer = client.post("/host/database/repair", headers=headers)
    assert answer.status_code == 409 and "no damage" in answer.json()["detail"]

    monkeypatch.setattr(
        db_health,
        "inspect",
        lambda path=None, full=False: db_health._verdict("index_pages", ["Page 9: never used"], set(), set()),
    )
    answer = client.post("/host/database/repair", headers=headers)
    assert answer.status_code == 409 and "rebuild" in answer.json()["detail"]


def _history_and_config(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
        conn.execute("INSERT INTO users (username) VALUES ('root')")
        conn.execute("CREATE TABLE metrics_samples (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO metrics_samples (v) VALUES (?)", [("x" * 200,) for _ in range(500)])
    _settle(path)


def test_a_damaged_history_table_is_emptied_only_when_asked(tmp_path):
    path = tmp_path / "x.db"
    _history_and_config(path)
    _write_page(path, _root(path, "metrics_samples"), b"\xff" * _page_size(path))

    with pytest.raises(db_health.RebuildFailed):
        db_health.rebuild(path)
    assert not list(tmp_path.glob("x.db.rebuild-*")) and not list(tmp_path.glob("x.db.damaged-*")), "nothing changed"

    result = db_health.rebuild(path, drop_history=True)
    assert result["emptied"] == ["metrics_samples"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT username FROM users").fetchall() == [("root",)]
        assert conn.execute("SELECT count(*) FROM metrics_samples").fetchone()[0] == 0


def test_a_damaged_configuration_table_cannot_be_rebuilt(tmp_path):
    path = tmp_path / "x.db"
    _history_and_config(path)
    _write_page(path, _root(path, "users"), b"\xff" * _page_size(path))
    with pytest.raises(db_health.RebuildFailed, match="users"):
        db_health.rebuild(path, drop_history=True)
    assert db_health.repair_for(db_health._verdict("tables", ["Tree 2 page 2"], set(), {"users"})) == "restore"
    assert (
        db_health.repair_for(db_health._verdict("tables", ["Tree 3 page 3"], set(), {"metrics_samples"})) == "rebuild"
    )


def test_repeated_corruption_errors_in_a_loop_run_the_full_check(database, logged):
    _stale_index(database.DB_PATH)
    malformed = _malformed()

    db_health.failed("metrics", _locked())
    for _ in range(db_health.REPEAT - 1):
        db_health.failed("metrics", malformed)
    assert db_health.summary()["status"] == "unknown", "not checked before REPEAT errors in a row"

    db_health.failed("metrics", malformed)
    state = db_health.summary()
    assert state["status"] == "damaged" and state["full"] and state["kind"] == "index_entries"
    assert state["loops"]["metrics"]["errors"] == db_health.REPEAT
    assert len([e for e in logged if e[1] == "database_check"]) == 1


def test_a_loop_failing_on_a_sound_file_is_shown_until_it_goes_through(database, logged):
    malformed = _malformed()
    for _ in range(db_health.REPEAT):
        db_health.failed("metrics", malformed)
    state = db_health.summary()
    assert state["status"] == "failing" and "metrics" in state["loops"]
    assert logged[-1][1:3] == ("database_check", "failed") and "metrics loop" in logged[-1][3]

    db_health.succeeded("metrics")
    assert db_health.summary()["status"] == "ok"
    assert logged[-1][1:3] == ("database_check", "success")


def test_an_error_that_is_not_corruption_never_counts(database, logged):
    for _ in range(db_health.REPEAT * 2):
        db_health.failed("metrics", _locked())
    assert db_health.summary() == {"status": "unknown", "checked_at": None, "full": False, "loops": {}}
    assert logged == []


def test_the_api_and_the_node_summary(database, client, auth_headers):
    admin = auth_headers("root")
    viewer = auth_headers("eve", role="viewer")
    assert client.get("/host/database", headers=viewer).json()["status"] == "unknown"
    assert client.post("/host/database/check", headers=viewer).status_code == 403
    checked = client.post("/host/database/check", headers=admin)
    assert checked.status_code == 200 and checked.json()["status"] == "ok" and checked.json()["full"]
    assert client.get("/host/database", headers=viewer).json()["status"] == "ok"


def test_the_command_line(tmp_path, monkeypatch, capsys):
    path = tmp_path / "x.db"
    _damaged_index_page(path)
    assert db_health.main(["check", "--db", str(path)]) == 1
    assert "repair: rebuild" in capsys.readouterr().out

    monkeypatch.setattr(db_health, "_service_active", lambda: True)
    assert db_health.main(["rebuild", "--db", str(path)]) == 3
    assert not db_health.inspect(path)["ok"], "never with the service running"

    monkeypatch.setattr(db_health, "_service_active", lambda: False)
    assert db_health.main(["rebuild", "--db", str(path)]) == 0
    assert db_health.main(["check", "--full", "--db", str(path)]) == 0
