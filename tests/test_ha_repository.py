"""The HA repository (control plane v2, lot 10): protected VMs, the watcher's settings and node fencing, run against
every backend (SQLite today)."""

import asyncio

import pytest


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.ha import SqliteHaRepository

    return SqliteHaRepository()


def run(coro):
    return asyncio.run(coro)


def test_protected_vms(repo):
    store = repo.sync
    store.protect("web", "n1", "<domain/>", "alice", "t0")
    store.protect("web", "n2", "<domain>2</domain>", "bob", "t1")  # re-enabling keeps who enabled it first
    vm = run(repo.protected_vm("web"))
    assert (vm["node"], vm["domain_xml"], vm["enabled_by"], vm["last_synced_at"]) == (
        "n2",
        "<domain>2</domain>",
        "alice",
        "t1",
    )
    store.sync_definition("web", "<d3/>", "t2")
    store.sync_definition("web", "<d4/>", "t3", node="n3")
    assert (store.protected_vm("web")["node"], store.protected_vm("web")["domain_xml"]) == ("n3", "<d4/>")
    assert store.move_protected("web", "n1") and not store.move_protected("ghost", "n1")
    store.record_watch("web", "suspect", "none", "t4")
    assert store.protected_vm("web")["etat_ha"] == "suspect"
    assert [v["vm_name"] for v in run(repo.protected())] == ["web"]
    store.unprotect("web")
    assert store.protected() == []


def test_watcher_settings(repo):
    store = repo.sync
    assert run(repo.settings()) == {}
    store.save_settings({"temoin": "10.0.0.1", "seuil_suspect": "3"})
    store.save_settings({"seuil_suspect": "4"})
    assert store.settings() == {"temoin": "10.0.0.1", "seuil_suspect": "4"}


def test_fencing_keeps_the_secret_sealed(repo):
    store = repo.sync
    store.save_fencing("n2", "ipmi", "10.0.0.9", None, "admin", "sealed", 0, "alice", "t0")
    store.save_fencing("n1", "lease_only", None, None, None, None, 0, "alice", "t0")
    assert store.fencing("n2")["secret"] == "sealed" and store.fenced_nodes() == ["n1", "n2"]
    assert store.delete_fencing("n2") and not store.delete_fencing("n2") and store.fencing("n2") is None
