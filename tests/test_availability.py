"""Availability of VMs and nodes (app/core/availability.py): only changes written, gaps unknown, stops made on purpose
not counted against it, the journal naming who stopped a VM."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import availability as av

T0 = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
STEP = 300  # rounds every 5 minutes here, with a gap of 15 minutes: the same rules, far fewer writes


@pytest.fixture(autouse=True)
def _coarse(monkeypatch):
    monkeypatch.setattr(av, "GAP_S", 900)


def _round(at, *obs):
    av.observe_round(list(obs), now=at)


def _every(start, end, step_s, *obs):
    t = start
    while t <= end:
        _round(t, *obs)
        t += timedelta(seconds=step_s)


def test_libvirt_states_are_read():
    assert av.from_libvirt(1, 1) == ("running", "booted")
    assert av.from_libvirt(5, 1) == ("stopped", "shutdown")
    assert av.from_libvirt(5, 3) == ("crashed", "crashed")
    assert av.from_libvirt(6, 1) == ("crashed", "panicked")
    assert av.from_libvirt(3, 1) == ("paused", "user")
    assert av.from_libvirt(3, 5) == ("crashed", "io-error")


def test_only_changes_are_written_and_measured(database):
    _every(T0, T0 + timedelta(hours=10), STEP, ("web", "running", "booted"))
    _every(T0 + timedelta(hours=10, seconds=STEP), T0 + timedelta(hours=12), STEP, ("web", "stopped", "shutdown"))
    _every(T0 + timedelta(hours=12, seconds=STEP), T0 + timedelta(hours=13), STEP, ("web", "crashed", "crashed"))
    _every(T0 + timedelta(hours=13, seconds=STEP), T0 + timedelta(hours=24), STEP, ("web", "running", "booted"))
    with database.get_conn() as db:
        assert db.execute("SELECT COUNT(*) FROM availability_events").fetchone()[0] == 4
    m = av.measure("web", days=1, now=T0 + timedelta(hours=24))
    assert m["planned_s"] == 2 * 3600
    assert m["unplanned_s"] == 3600
    assert m["interruptions"] == 1
    # Available: running over the time it was meant to run (the stop on purpose left out).
    assert abs(m["available_pct"] - 21 / 22 * 100) < 0.01
    assert abs(m["uptime_pct"] - 21 / 24 * 100) < 0.01


def test_a_gap_is_unknown_and_unplanned_after_running(database):
    _every(T0, T0 + timedelta(hours=1), STEP, ("web", "running", ""))
    _every(T0 + timedelta(hours=2), T0 + timedelta(hours=3), STEP, ("web", "running", ""))  # nobody watched for 1 h
    m = av.measure("web", days=1, now=T0 + timedelta(hours=3))
    assert [p["state"] for p in m["periods"]] == ["running", "unknown", "running"]
    assert m["periods"][1]["kind"] == "unplanned"
    assert av.current("web", T0 + timedelta(hours=3))["state"] == "running"
    assert av.current("web", T0 + timedelta(hours=4))["state"] == "unknown"


def test_unknown_after_a_stop_stays_planned(database):
    _every(T0, T0 + timedelta(minutes=10), STEP, ("db", "stopped", "shutdown"))
    _every(T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=10), STEP, ("db", "stopped", "shutdown"))
    m = av.measure("db", days=1, now=T0 + timedelta(hours=2, minutes=10))
    assert m["unplanned_s"] == 0 and m["available_pct"] is None


def test_the_report_names_who_stopped_a_vm(database, monkeypatch):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO audit_log (timestamp, username, action, resource, result) VALUES (?, 'ann', 'stop_vm', 'web', "
            "'success')",
            ((T0 + timedelta(hours=1, seconds=STEP - 20)).isoformat(),),
        )
        db.commit()
    _every(T0, T0 + timedelta(hours=1), STEP, ("web", "running", ""))
    _every(T0 + timedelta(hours=1, seconds=STEP), T0 + timedelta(hours=2), STEP, ("web", "stopped", "shutdown"))
    r = av.report("vm", "web", None, days=1, now=T0 + timedelta(hours=2))
    assert r["now"]["state"] == "stopped"
    assert r["events"][0]["by"] == {"username": "ann", "action": "stop_vm"}
    assert set(r["windows"]) == {"1", "7", "30"}


def test_availability_routes_follow_rights(client, auth_headers, monkeypatch):
    from app.core import permissions

    av.observe_round([("web", "running", ""), ("secret", "running", ""), ("host", "running", "")])
    viewer = auth_headers("eve", role="viewer")
    r = client.get("/availability/vm/web", headers=viewer)
    assert r.status_code == 200 and r.json()["now"]["state"] == "running"
    assert client.get("/availability/disk/web", headers=viewer).status_code == 404
    monkeypatch.setattr(permissions, "has_privilege", lambda user, name, p: name != "secret")
    names = [x["name"] for x in client.get("/availability/summary", headers=viewer).json()["items"]]
    assert "web" in names and "secret" not in names


def test_the_metrics_round_hands_each_vm_state():
    from app.core import metrics

    class Dom:
        def __init__(self, state):
            self._s = state

        def state(self):
            return self._s

    seen = []
    metrics._observe(Dom((5, 3)), "hv-02:db", seen)
    metrics._observe(Dom((1, 1)), "web", seen)
    assert seen == [("hv-02:db", "crashed", "crashed"), ("web", "running", "booted")]
