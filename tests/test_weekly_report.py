"""Weekly report (app/core/weekly_report.py): what it sums up, when it is due, and that the leader sends it once."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import weekly_report

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def test_the_report_sums_up_the_week():
    facts = {
        "backups": [
            {"vm_name": "db", "status": "succeeded", "size_bytes": 2 * 1024**3, "error": None},
            {"vm_name": "web", "status": "failed", "size_bytes": None, "error": "No space"},
        ],
        "journal": [
            {"action": "alert_rule_fired", "result": "failed", "resource": "db (hv-02)"},
            {"action": "alert_rule_fired", "result": "failed", "resource": "db (hv-02)"},
            {"action": "migrate_vm", "result": "failed", "resource": "api"},
            {"action": "login", "result": "failed", "resource": "x"},
        ],
        "load": [
            {"target": "host", "scope": "host", "cpu_avg": 20.04, "cpu_max": 70, "mem_max": 81.26},
            {"target": "hv-02:db", "scope": "vm", "cpu_avg": 55, "cpu_max": 98},
            {"target": "web", "scope": "vm", "cpu_avg": 5, "cpu_max": 9},
        ],
        "pools": [{"node": "hv-test", "pool": "nas", "capacity_b": 1000, "allocation_b": 925}],
    }
    r = weekly_report.build(facts, NOW)
    assert r["backups"] == {"succeeded": 1, "failed": 1, "bytes": 2 * 1024**3, "failed_vms": ["web"]}
    assert r["alerts"] == [("db (hv-02)", 2)] and r["failures"] == [("alert_rule_fired", 2), ("migrate_vm", 1)]
    assert [v["vm"] for v in r["busiest_vms"]] == ["db", "web"]
    assert r["nodes"][0] == {"node": "this node", "cpu_avg": 20.0, "cpu_max": 70, "mem_max": 81.3}
    text = weekly_report.text(r)
    assert "Backups: 1 succeeded (2.0 GiB), 1 failed" in text and "nas (hv-test): 92.5 %" in text


def test_it_is_due_once_on_the_chosen_day_and_hour():
    conf = {"enabled": True, "weekday": NOW.astimezone().weekday(), "hour": NOW.astimezone().hour}
    assert weekly_report.due(NOW, conf, None)
    assert not weekly_report.due(NOW, {**conf, "enabled": False}, None)
    assert not weekly_report.due(NOW, {**conf, "hour": (conf["hour"] + 1) % 24}, None) or conf["hour"] == 23
    assert not weekly_report.due(NOW, conf, (NOW - timedelta(days=1)).isoformat())
    assert weekly_report.due(NOW, conf, (NOW - timedelta(days=7)).isoformat())


def test_settings_are_checked(database):
    assert weekly_report.settings() == weekly_report.DEFAULTS
    assert weekly_report.save_settings(True, 4, 18) == {"enabled": True, "weekday": 4, "hour": 18}
    for weekday, hour in ((7, 8), (0, 24)):
        with pytest.raises(ValueError):
            weekly_report.save_settings(True, weekday, hour)


def test_the_leader_sends_it_once(database, monkeypatch):
    sent = []
    monkeypatch.setattr("app.core.notifications.notify", lambda event, title, message, result: sent.append(event))
    local = NOW.astimezone()
    weekly_report.save_settings(True, local.weekday(), local.hour)
    weekly_report.tick(NOW)
    weekly_report.tick(NOW + timedelta(minutes=5))
    assert sent == ["weekly_report"] and weekly_report.last_sent() == NOW.isoformat()
    monkeypatch.setattr("app.core.cluster_lead.is_leader", lambda: False)
    weekly_report.tick(NOW + timedelta(days=7))
    assert sent == ["weekly_report"]


def test_the_api_previews_and_sends(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.core.notifications.notify", lambda *a: None)
    admin, viewer = auth_headers("root"), auth_headers("eve", role="viewer")
    assert client.get("/reports/weekly", headers=viewer).status_code == 403
    body = client.get("/reports/weekly", headers=admin).json()
    assert body["settings"]["enabled"] is False and body["text"].startswith("Week from")
    assert (
        client.put(
            "/reports/weekly/settings", json={"enabled": True, "weekday": 9, "hour": 8}, headers=admin
        ).status_code
        == 422
    )
    assert client.post("/reports/weekly/send", headers=admin).json() == {"sent": True}
