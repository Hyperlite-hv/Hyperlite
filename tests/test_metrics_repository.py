"""The metrics repository (control plane v2, lot 3): samples, their hourly roll-up and retention, and the export
servers, run against every backend (SQLite today)."""

import asyncio

import pytest


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.metrics import SqliteMetricsRepository

    return SqliteMetricsRepository()


def run(coro):
    return asyncio.run(coro)


def test_samples_history_latest_and_rollup(repo):
    store = repo.sync
    store.write_samples(
        "2026-09-30T10:00:00", [("vm", "web", 10.0, 1, 2, 0, 0, 0, 0)], [("local", "default", 100.0, 10.0)]
    )
    store.write_samples(
        "2026-09-30T10:00:15", [("vm", "web", 30.0, 1, 2, 0, 0, 0, 0)], [("local", "default", 100.0, 30.0)]
    )
    assert [r["cpu_pct"] for r in run(repo.history("web", "raw", "2026-09-30T00:00:00"))] == [10.0, 30.0]
    assert run(repo.latest_by_cible())[0]["cpu_pct"] == 30.0
    assert run(repo.latest_tick()) == [("vm", "web", 30.0, 1, 2, 0, 0, 0, 0)]
    store.rollup_and_prune("2026-09-30T11:00:00", "2026-09-30T09:59:00", "2026-09-30T10:00:10", "2026-01-01T00:00:00")
    assert [r["cpu_pct"] for r in run(repo.history("web", "hourly", "2026-09-30T00:00:00"))] == [20.0]
    assert [r["cpu_pct"] for r in run(repo.history("web", "raw", "2026-09-30T00:00:00"))] == [30.0]  # older raw pruned
    storage = run(repo.storage_history("hourly", "2026-09-30T00:00:00", "local"))
    assert [(r["pool"], r["allocation_b"]) for r in storage] == [("default", 20.0)]


def test_export_servers(repo):
    store = repo.sync
    values = ["influx", "influxdb", "http://i:8086", None, None, "org", "bucket", "hyperlite", 1]
    server_id = store.save_server(values, "encrypted", None)
    assert store.name_taken("influx") and not store.name_taken("influx", server_id)
    store.save_server(["influx", "influxdb", "http://i:8086", None, None, "org", "b2", "hyperlite", 0], None, server_id)
    server = store.get_server(server_id)
    assert (server["bucket"], server["jeton"], server["actif"]) == ("b2", "encrypted", 0)  # the token is kept
    assert store.list_servers(active_only=True) == []
    store.record_send(server_id, error="refused")
    assert store.get_server(server_id)["derniere_erreur"] == "refused"
    store.record_send(server_id, sent_at="t")
    assert (store.get_server(server_id)["dernier_envoi"], store.get_server(server_id)["derniere_erreur"]) == ("t", None)
    assert store.delete_server(server_id) and not store.delete_server(server_id)
