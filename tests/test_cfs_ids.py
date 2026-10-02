"""Integer ids that two nodes of a cluster never both hand out (app/repositories/cfs/ids.py): the next id of every
replicated table is reserved ahead from the daemon's cluster-wide counter."""

import sqlite3

import pytest

from app.core.database import get_conn
from app.repositories.cfs import ids, inbound, shadow
from tests.test_cfs_inbound import Cluster, cluster  # noqa: F401  (fixture)


def new_group(name):
    with get_conn() as db:
        cur = db.execute("INSERT INTO groups (name) VALUES (?)", (name,))
        db.commit()
    return cur.lastrowid


def test_outside_a_cluster_sqlite_numbers_rows_as_before(database, monkeypatch):
    monkeypatch.delenv("HYPERLITE_CFS_SHADOW", raising=False)
    assert [new_group("a"), new_group("b")] == [1, 2]


def test_a_daemon_in_local_mode_reserves_nothing(database, monkeypatch):
    fake = Cluster()
    fake.mode = "local"
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    shadow.seed()
    assert new_group("a") == 1
    assert fake.counter == 100 and ids.OFFSET_PATH not in fake.files


def test_ids_start_above_the_first_nodes_and_come_from_the_cluster_counter(cluster):  # noqa: F811
    # The seed of the fixture wrote the offset: above every id this node had (none yet), rounded.
    assert cluster.files[ids.OFFSET_PATH] == b"1000"
    first = new_group("ops")
    second = new_group("dev")
    assert 1100 <= first < second  # the counter starts at 100, above the offset

    # Another node takes the next ids and creates a group: it reaches this node, whose next id is still unique.
    taken = cluster.next_id() + 1000
    shadow.drain()
    cluster.remote(f"/db/groups/{taken}", {"id": taken, "name": "from-b"})
    inbound.apply()
    third = new_group("qa")
    assert third > taken
    with get_conn() as db:
        assert len({r[0] for r in db.execute("SELECT id FROM groups")}) == 4


def test_the_offset_written_by_the_first_node_holds(cluster):  # noqa: F811
    cluster.files[ids.OFFSET_PATH] = b"5000"
    ids.forget()
    with get_conn() as db:
        ids.set_offset(db, cluster)  # a second node's seed
    assert cluster.files[ids.OFFSET_PATH] == b"5000"
    new_group("ops")  # the id this node reserved before still is its own
    assert new_group("dev") > 5000


def test_existing_ids_lift_the_offset(database, monkeypatch):
    for n in range(3):
        new_group(f"g{n}")
    with get_conn() as db:
        db.execute("INSERT INTO groups (id, name) VALUES (2345, 'big')")
        db.commit()
    fake = Cluster()
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    shadow.seed()
    assert fake.files[ids.OFFSET_PATH] == b"3000"
    assert new_group("next") > 3000


def test_without_the_daemon_a_cluster_node_refuses_new_rows(cluster, monkeypatch):  # noqa: F811
    assert new_group("ok")

    def down():
        raise ConnectionRefusedError("down")

    monkeypatch.setattr(cluster, "next_id", down)
    with pytest.raises(sqlite3.OperationalError):
        new_group("lost")
    with get_conn() as db:
        assert db.execute("SELECT COUNT(*) FROM groups WHERE name = 'lost'").fetchone()[0] == 0


def test_the_numbered_tables_are_the_replicated_ones_with_autoincrement(database):
    with get_conn() as db:
        tables = set(ids.numbered_tables(db))
    assert {"groups", "users", "backup_jobs", "replication_jobs", "nodes", "jobs", "job_steps"} <= tables
    assert not tables & {"audit_log", "tasks", "backups", "cfs_outbox"}  # per node: numbered as before


def test_every_way_of_inserting_reserves_one_id_per_row(cluster):  # noqa: F811
    with get_conn() as db:
        db.cursor().execute("INSERT INTO groups (name) VALUES ('one')")
        db.executemany("INSERT INTO groups (name) VALUES (?)", [("two",), ("three",)])
        db.cursor().executemany('INSERT OR IGNORE INTO "groups" (name) VALUES (?)', [("four",), ("five",)])
        db.commit()
        got = [r[0] for r in db.execute("SELECT id FROM groups ORDER BY id")]
    assert len(got) == 5 and all(i > 1000 for i in got)
    assert cluster.counter == 105  # exactly one reservation per row


def test_rows_applied_from_the_tree_reserve_nothing(cluster):  # noqa: F811
    before = cluster.counter
    cluster.remote("/db/groups/4242", {"id": 4242, "name": "from-b"})
    assert inbound.apply() == 1
    assert cluster.counter == before


def test_the_api_answers_503_when_no_id_can_be_reserved(cluster, client, auth_headers, monkeypatch):  # noqa: F811
    headers = auth_headers("alice")

    def down():
        raise ConnectionRefusedError("down")

    monkeypatch.setattr(cluster, "next_id", down)
    r = client.post("/groups", json={"name": "ops"}, headers=headers)
    assert r.status_code == 503 and "does not answer" in r.json()["detail"]
