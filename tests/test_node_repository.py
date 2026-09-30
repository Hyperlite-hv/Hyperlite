"""The NodeRepository contract, run against every backend (SQLite today; etcd joins the list in phase 5)."""

import asyncio

import pytest

from app.domain.common import AlreadyExists
from app.domain.node import NodeSpec


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.nodes import SqliteNodeRepository

    return SqliteNodeRepository()


def run(coro):
    return asyncio.run(coro)


def test_create_get_list_and_delete(repo):
    node = run(repo.create(NodeSpec(name="n2", hostname="10.0.0.2"), "2026-09-30T00:00:00", "en_ligne"))
    assert node.name == "n2" and node.spec.ssh_port == 22 and node.status.statut == "en_ligne"
    run(repo.create(NodeSpec(name="n1", hostname="10.0.0.1", ssh_port=2222), "2026-09-30T00:00:00", "hors_ligne"))
    with pytest.raises(AlreadyExists):
        run(repo.create(NodeSpec(name="n2", hostname="x"), "t", "en_ligne"))
    assert [n.name for n in run(repo.list())] == ["n1", "n2"]
    assert [n.name for n in run(repo.list_online())] == ["n2"]
    assert run(repo.get("n1")).to_wire()["ssh_port"] == 2222 and run(repo.get("nope")) is None
    assert run(repo.delete("n1")) is True and run(repo.delete("n1")) is False


def test_status_updates_return_the_previous_state(repo):
    node = run(repo.create(NodeSpec(name="n2", hostname="10.0.0.2"), "t0", "en_ligne"))
    assert run(repo.update_status(node.id, "hors_ligne", "t1")) == "en_ligne"
    assert run(repo.get("n2")).status.model_dump() == {"statut": "hors_ligne", "derniere_verification": "t1"}
    assert run(repo.update_status(999, "en_ligne", "t2")) is None


def test_live_figures(repo):
    row = ("n2", "t", 1, 12.5, 1024.0, 2048.0, 60, 4, "cpu", "6.12", "Debian", "10.0.0.2", 1, 2)
    run(repo.write_live([row]))
    live = run(repo.live("n2"))
    assert live["joignable"] is True and live["cpu_pct"] == 12.5 and live["mesure_le"] == "t" and "name" not in live
    assert set(run(repo.live())) == {"n2"} and run(repo.live("nope")) is None


def test_maintenance_is_idempotent(repo):
    run(repo.set_maintenance("n2", "alice", "t1"))
    run(repo.set_maintenance("n2", "bob", "t2"))  # the first start is kept
    assert run(repo.get_maintenance("n2")).to_wire() == {"node": "n2", "started_by": "alice", "started_at": "t1"}
    assert [m.node for m in run(repo.list_maintenance())] == ["n2"]
    assert run(repo.clear_maintenance("n2")) is True and run(repo.clear_maintenance("n2")) is False


def test_leases_wait_for_etcd(repo):
    with pytest.raises(NotImplementedError):
        run(repo.acquire_lease("n2", 10))
