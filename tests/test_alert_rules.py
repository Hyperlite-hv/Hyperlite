"""Alert rules (app/core/alert_rules.py, #348): checked when saved, judged on the average over their window, fired
once per target and resolved, notified to the channels they name, and evaluated by the leader only."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import alert_rules, self_node

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _sample(target, scope, minutes_ago, **values):
    base = {
        "cpu_pct": None,
        "mem_used_mb": None,
        "mem_total_mb": None,
        "disk_read_bps": None,
        "disk_write_bps": None,
        "net_rx_bps": None,
        "net_tx_bps": None,
    }
    return {
        **base,
        **values,
        "target": target,
        "scope": scope,
        "ts": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
    }


def _rule(**over):
    return {
        "name": "cpu",
        "scope": "all_vms",
        "metric": "cpu_pct",
        "operator": "gt",
        "threshold": 80,
        "window_min": 5,
        **over,
    }


@pytest.mark.parametrize(
    ("over", "error"),
    [
        ({"name": ""}, "name"),
        ({"scope": "cluster"}, "scope"),
        ({"scope": "node", "node": "bad name"}, "node name"),
        ({"scope": "vm", "node": "local", "vm": "a;b"}, "VM name"),
        ({"metric": "temperature"}, "metric"),
        ({"operator": "ge"}, "operator"),
        ({"threshold": 150}, "out of range"),
        ({"threshold": "high"}, "number"),
        ({"window_min": 7}, "window"),
        ({"severity": "fatal"}, "severity"),
        ({"channels": ["x"]}, "channels"),
    ],
)
def test_a_rule_is_checked_when_saved(database, over, error):
    with pytest.raises(ValueError, match=error):
        alert_rules.create(_rule(**over), "admin")


def test_this_node_is_stored_by_name_and_read_back_as_local(database):
    r = alert_rules.create(_rule(scope="vm", node="local", vm="db"), "admin")
    assert r["node"] == "local" and r["firing"] == [] and r["enabled"]
    assert alert_rules._store().rule(r["id"])["node"] == self_node.name()


def test_the_average_over_the_window_decides(database):
    me = self_node.name()
    r = alert_rules.validate(_rule())
    since = (NOW - timedelta(minutes=5)).isoformat()
    samples = [
        _sample("db", "vm", 4, cpu_pct=90),
        _sample("db", "vm", 2, cpu_pct=85),  # avg 87.5: holds
        _sample("hv-02:web", "vm", 4, cpu_pct=95),
        _sample("hv-02:web", "vm", 1, cpu_pct=50),  # avg 72.5: does not
        _sample("cache", "vm", 1, cpu_pct=99),  # a single sample is not judged
        _sample("old", "vm", 9, cpu_pct=99),
        _sample("old", "vm", 8, cpu_pct=99),  # outside the window
        _sample("host", "host", 1, cpu_pct=99),
        _sample("host", "host", 2, cpu_pct=99),  # a node, not a VM
    ]
    assert alert_rules.check(r, samples, since, me) == {f"{me}:db": 87.5}
    nodes = alert_rules.validate(_rule(scope="node", node="hv-02", metric="mem_pct", operator="lt", threshold=20))
    low = [
        _sample("node:hv-02", "host", 1, mem_used_mb=100, mem_total_mb=1000),
        _sample("node:hv-02", "host", 2, mem_used_mb=150, mem_total_mb=1000),
    ]
    assert alert_rules.check(nodes, low, since, me) == {"hv-02": 12.5}


def test_a_rule_fires_once_per_target_then_resolves(database, monkeypatch):
    sent, logged = [], []
    monkeypatch.setattr(alert_rules, "_send", lambda rule, title, message, result: sent.append((title, result)))
    monkeypatch.setattr("app.core.audit.log_action", lambda *a, **k: logged.append(a[1]))
    r = alert_rules.create(_rule(name="hot", severity="critical"), "admin")
    me = self_node.name()
    hot = [_sample("db", "vm", 1, cpu_pct=95), _sample("db", "vm", 2, cpu_pct=95)]
    monkeypatch.setattr("app.repositories.sqlite.metrics.SqliteMetricsStore.recent_raw", lambda self, since: hot)
    alert_rules.evaluate(NOW)
    alert_rules.evaluate(NOW)  # still firing: not notified again
    assert sent == [(f"[critical] hot: db ({me})", "failed")]
    assert alert_rules.rule(r["id"])["firing_labels"] == [f"db ({me})"]
    hot[:] = [_sample("db", "vm", 1, cpu_pct=10), _sample("db", "vm", 2, cpu_pct=10)]
    alert_rules.evaluate(NOW)
    assert sent[-1] == (f"[resolved] hot: db ({me})", "success")
    assert logged == ["alert_rule_fired", "alert_rule_resolved"]
    assert alert_rules.rule(r["id"])["firing"] == []


def test_only_the_leader_evaluates(database, monkeypatch):
    monkeypatch.setattr("app.core.cluster_lead.is_leader", lambda: False)
    alert_rules.create(_rule(), "admin")
    called = []
    monkeypatch.setattr(
        "app.repositories.sqlite.metrics.SqliteMetricsStore.recent_raw", lambda self, since: called.append(since) or []
    )
    alert_rules.evaluate(NOW)
    assert called == []


def test_a_rule_naming_channels_goes_to_those_only(database, monkeypatch):
    from app.core import notifications

    channels = [
        {"id": 1, "name": "ops", "enabled": True},
        {"id": 2, "name": "all", "enabled": True},
        {"id": 3, "name": "off", "enabled": False},
    ]
    got = []
    monkeypatch.setattr(notifications, "list_channels", lambda: channels)
    monkeypatch.setattr(notifications, "send_to_channel", lambda c, *a: got.append(c["name"]))
    monkeypatch.setattr(notifications, "notify", lambda *a: got.append("broadcast"))
    alert_rules._send({"channels": [1, 3]}, "t", "m", "failed")
    alert_rules._send({"channels": []}, "t", "m", "failed")
    assert got == ["ops", "broadcast"]


def test_the_api_lists_and_writes_rules(client, auth_headers):
    admin, viewer = auth_headers("root"), auth_headers("eve", role="viewer")
    r = client.post("/alert-rules", json=_rule(name="net", metric="net_rx_bps", threshold=1e8), headers=admin)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert [x["name"] for x in client.get("/alert-rules", headers=viewer).json()["items"]] == ["net"]
    assert client.post("/alert-rules", json=_rule(), headers=viewer).status_code == 403
    assert client.put(f"/alert-rules/{rid}/enabled", json={"enabled": False}, headers=admin).json()["enabled"] is False
    assert client.post("/alert-rules", json=_rule(window_min=3), headers=admin).status_code == 422
    assert client.delete(f"/alert-rules/{rid}", headers=admin).status_code == 204
    assert client.delete(f"/alert-rules/{rid}", headers=admin).status_code == 404
