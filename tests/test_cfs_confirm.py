"""Confirmed writes in a cluster (docs/design/confirmed-writes.md, app/repositories/cfs/confirm.py, shadow.confirm,
the confirm_writes middleware): a change answers once the cluster took it, 409 when another node changed the same
thing first, 503 when the cluster does not take it in time; never a success undone a second later."""

import json
import threading
import time

import pytest

from app.core import audit, cluster_lead
from app.core.cfs_client import ReadOnly
from app.core.database import get_conn
from app.repositories.cfs import confirm, ids, inbound, shadow
from tests.test_cfs_inbound import Cluster, rows


@pytest.fixture()
def fake(database, monkeypatch):
    fake = Cluster()
    monkeypatch.setenv("HYPERLITE_CFS_SHADOW", "1")
    monkeypatch.setattr(shadow, "_get_client", lambda: fake)
    monkeypatch.setattr(shadow, "_stats", shadow._Stats())
    monkeypatch.setattr(inbound, "state", inbound._State())
    monkeypatch.setattr(cluster_lead, "STATUS_TTL_S", 0)
    monkeypatch.setattr(cluster_lead, "QUORUM_TTL_S", 0)
    cluster_lead.forget()
    ids.forget()
    shadow.seed()
    fake.remote("/db/groups/7", {"id": 7, "name": "ops"})
    inbound.apply()  # this node holds group 7 at the cluster's version
    return fake


@pytest.fixture()
def notified(monkeypatch):
    sent = []
    monkeypatch.setattr("app.core.notifications.notify", lambda event, *a, **k: sent.append(event))
    return sent


@pytest.fixture()
def admin(auth_headers, fake):
    headers = auth_headers("ana")
    shadow.drain()  # the account made for the test is in the cluster
    return headers


def group_7():
    return json.loads(fake_files()["/db/groups/7"]) if "/db/groups/7" in fake_files() else None


def fake_files():
    return shadow._get_client().files


def test_a_change_starts_from_what_the_cluster_changed_just_before(client, admin, fake):
    """Lab check 1c: renamed on another node, deleted here a second later. The delete used to answer 200 and be undone
    (it started from the old version); the node now applies the rename first, and the delete holds."""
    fake.remote("/db/groups/7", {"id": 7, "name": "renamed-elsewhere"})  # not applied here yet
    r = client.delete("/groups/7", headers=admin)
    assert r.status_code == 200, r.text
    assert "/db/groups/7" not in fake.files and rows("SELECT id FROM groups") == []
    assert shadow.report()["conflits"] == 0 and shadow.pending() == 0


def test_another_nodes_change_that_comes_first_is_a_409(client, admin, fake, notified, monkeypatch):
    # The other node's rename arrives after this node caught up and before its own change goes out.
    monkeypatch.setattr("app.main._catch_up", lambda: fake.remote("/db/groups/7", {"id": 7, "name": "theirs"}))
    r = client.patch("/groups/7", json={"name": "mine"}, headers=admin)
    assert r.status_code == 409 and r.json()["code"] == "changed_elsewhere", r.text
    assert r.headers["x-frame-options"] == "DENY"  # made outside the handler, still with the security headers
    assert json.loads(fake.files["/db/groups/7"])["name"] == "theirs"
    assert rows("SELECT name FROM groups WHERE id = 7") == [{"name": "theirs"}]  # back here before the answer
    audit._AUDIT_QUEUE.join()
    assert rows("SELECT resource FROM audit_log WHERE action = 'cfs_conflict'") == [{"resource": "/db/groups/7"}]
    assert notified == []  # the person was told by the 409


def test_a_background_change_the_cluster_overrode_is_notified(fake, notified):
    fake.remote("/db/groups/7", {"id": 7, "name": "theirs"})
    with get_conn() as db:  # a task or a scheduler, not a request
        db.execute("UPDATE groups SET name = 'by-a-task' WHERE id = 7")
        db.commit()
    shadow.drain()
    for _ in range(50):
        if notified:
            break
        time.sleep(0.05)
    assert notified == ["cfs_conflict"]


def test_a_change_the_cluster_does_not_take_in_time_is_a_503_and_waits(client, admin, fake, monkeypatch):
    monkeypatch.setattr(shadow, "CONFIRM_S", 0.5)
    put = fake.put

    def stuck(*a, **k):
        raise TimeoutError("the daemon does not answer")

    monkeypatch.setattr(fake, "put", stuck)
    r = client.patch("/groups/7", json={"name": "slow"}, headers=admin)
    assert r.status_code == 503 and r.json()["code"] == "not_confirmed", r.text
    assert shadow.pending() == 2  # kept for the copy loop (the row before and after the change)
    monkeypatch.setattr(fake, "put", put)
    assert shadow.drain() == 1 and json.loads(fake.files["/db/groups/7"])["name"] == "slow"


def test_a_change_without_quorum_is_refused_at_once(client, admin, fake, monkeypatch):
    """Lab check 7a: cut off in the same second, the quorum still read as present from a cache; the daemon refuses the
    copy and the answer says so at once, not 200."""

    def read_only(*a, **k):
        raise ReadOnly(5, "no quorum")

    monkeypatch.setattr(fake, "put", read_only)
    start = time.monotonic()
    r = client.patch("/groups/7", json={"name": "cut-off"}, headers=admin)
    assert r.status_code == 503 and "quorum" in r.json()["detail"], r.text
    assert time.monotonic() - start < shadow.CONFIRM_S / 2


def test_a_single_node_answers_without_waiting(client, admin, fake):
    fake.mode = "local"
    cluster_lead.forget()
    r = client.patch("/groups/7", json={"name": "alone"}, headers=admin)
    assert r.status_code == 200 and shadow.pending() == 2  # copied by the loop, as before


def test_a_refused_request_waits_for_nothing(client, admin, fake):
    client.post("/groups", json={"name": "dev"}, headers=admin)
    r = client.patch("/groups/7", json={"name": "dev"}, headers=admin)  # the name is taken
    assert r.status_code == 409 and "code" not in r.json()


def test_only_the_requests_own_entries_are_noted(fake):
    noted, token = confirm.begin()
    try:
        with get_conn() as db:
            db.execute("SELECT * FROM groups").fetchall()
            assert not db.in_transaction  # a read takes no lock
            db.execute("UPDATE groups SET name = 'mine' WHERE id = 7")

            def other():  # another thread, outside the request: waits for the request's write lock
                with get_conn() as own:
                    own.execute("INSERT INTO groups (name) VALUES ('theirs')")
                    own.commit()

            elsewhere = threading.Thread(target=other)
            elsewhere.start()
            time.sleep(0.2)
            db.commit()
        elsewhere.join()
    finally:
        confirm.end(token)
    with get_conn() as db:
        mine = {r[0] for r in db.execute("SELECT id FROM cfs_outbox WHERE pk = '[7]'")}
        assert noted.ids == mine and len(mine) == 2  # the old and the new row of the update
        assert db.execute("SELECT COUNT(*) FROM cfs_outbox").fetchone()[0] == 3
