"""The settings repository (control plane v2, lot 11): network firewalls, shared pools, Kubernetes clusters,
notification channels, the update check, application settings and configuration copies, run against every backend
(SQLite today)."""

import asyncio

import pytest


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.settings import SqliteSettingsRepository

    return SqliteSettingsRepository()


def run(coro):
    return asyncio.run(coro)


def test_network_firewalls(repo):
    store = repo.sync
    store.save_network_firewall("lan", "drop", "[]")
    store.save_network_firewall("lan", "accept", '[{"port": 22}]')
    assert store.network_firewall("lan")["rules_json"] == '[{"port": 22}]' and len(store.network_firewalls()) == 1
    store.delete_network_firewall("lan")
    assert store.network_firewall("lan") is None


def test_shared_pools(repo):
    store = repo.sync
    store.save_shared_pool("nfs", '{"name": "nfs"}', 1, '["n1"]', "alice", "t0")
    store.save_shared_pool("iscsi", '{"name": "iscsi"}', 0, "[]", "alice", "t0")
    assert [p["nom"] for p in run(repo.shared_pools())] == ["iscsi", "nfs"]
    assert [p["nom"] for p in store.shared_pools(every_node_only=True)] == ["nfs"]
    store.set_shared_pool_nodes("nfs", '["n1", "n2"]')
    store.rename_shared_pool("nfs", "nas", '{"name": "nas"}')
    assert store.shared_pool("nfs") is None and store.shared_pool("nas")["noeuds"] == '["n1", "n2"]'
    store.delete_shared_pool("nas")
    assert store.shared_pool("nas") is None


def test_k8s_clusters(repo):
    store = repo.sync
    store.create_k8s_cluster("k", "lan", "k-server", '["k-worker-1"]', 2, 2048, 20, "task", "alice", "t0")
    store.update_k8s_cluster("k", {"statut": "pret", "kubeconfig": "sealed"})
    with pytest.raises(ValueError):
        store.update_k8s_cluster("k", {"nom": "x"})
    assert run(repo.k8s_clusters())[0]["statut"] == "pret"
    store.update_k8s_cluster("k", {"statut": "creation"})
    store.fail_interrupted_k8s_clusters("Interrupted")
    assert (store.k8s_cluster("k")["statut"], store.k8s_cluster("k")["erreur"]) == ("echec", "Interrupted")
    store.delete_k8s_cluster("k")
    assert store.k8s_clusters() == []


def test_notification_channels(repo):
    store = repo.sync
    cid = store.create_channel("webhook", "ops", '{"url": "https://x"}', "[]", "alice", "t0")
    store.update_channel(cid, {"name": "ops2", "enabled": 0})
    store.update_channel(cid, {})
    with pytest.raises(ValueError):
        store.update_channel(cid, {"type": "email"})
    assert (store.channel(cid)["name"], store.channel(cid)["enabled"]) == ("ops2", 0)
    assert [c["id"] for c in run(repo.channels())] == [cid]
    assert store.delete_channel(cid) and not store.delete_channel(cid)


def test_update_check_settings_and_config_copies(repo):
    store = repo.sync
    assert store.last_notified_version() is None
    store.touch_update_checked("t0")
    store.mark_update_notified("2026.10.01", "t1")
    store.touch_update_checked("t2")
    assert store.last_notified_version() == "2026.10.01"
    assert run(repo.app_setting("k")) is None
    store.set_app_setting("k", "v1")
    store.set_app_setting("k", "v2")
    assert store.app_setting("k") == "v2"
    store.record_config_copy("n1", "t0", "ok", 10, "abc", None)
    store.record_config_copy("n1", "t1", "echec", None, None, "ssh")
    copy = store.config_copies()[0]
    assert (copy["statut"], copy["taille"], copy["empreinte"], copy["erreur"]) == ("echec", 10, "abc", "ssh")
