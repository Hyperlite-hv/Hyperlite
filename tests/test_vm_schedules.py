"""VM schedules (app/core/vm_schedules.py): their next time on the chosen weekdays, run by the node hosting the VM,
snapshots kept to their number, a late run skipped, and the rights each action needs."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import vm_schedules


def test_the_next_time_falls_on_a_chosen_weekday():
    def local(*a):  # the node's local time, as schedules read it
        return datetime(*a).astimezone().astimezone(UTC)

    wed = local(2026, 11, 11, 10, 0)  # a Wednesday
    assert vm_schedules.next_run([2], "12:30", wed) == local(2026, 11, 11, 12, 30)
    assert vm_schedules.next_run([2], "09:00", wed) == local(2026, 11, 18, 9, 0)
    assert vm_schedules.next_run([4, 5], "07:00", wed) == local(2026, 11, 13, 7, 0)


@pytest.mark.parametrize(
    ("over", "error"),
    [
        ({"action": "reboot"}, "action"),
        ({"days": []}, "Days"),
        ({"days": [7]}, "Days"),
        ({"time": "25:00"}, "HH:MM"),
        ({"keep": 0}, "Keep"),
        ({"vm_name": "a b"}, "VM name"),
    ],
)
def test_a_schedule_is_checked(database, over, error):
    with pytest.raises(ValueError, match=error):
        vm_schedules.create({"vm_name": "web", "action": "start", "days": [0], "time": "08:00", **over}, "admin")


@pytest.fixture()
def runs(database, monkeypatch):
    calls = []
    monkeypatch.setattr(vm_schedules, "_hosted", lambda: {"web"})
    monkeypatch.setattr(
        "app.core.privileged.run", lambda op, **kw: calls.append((op, kw.get("name") or kw.get("action")))
    )
    return calls


def _due(s, minutes_ago=1):
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    vm_schedules._store().record(s["id"], None, None, s["made"], when.isoformat())


def test_a_power_schedule_runs_on_the_hosting_node_only(runs):
    here = vm_schedules.create(
        {"vm_name": "web", "action": "shutdown", "days": list(range(7)), "time": "20:00"}, "admin"
    )
    away = vm_schedules.create({"vm_name": "db", "action": "start", "days": list(range(7)), "time": "20:00"}, "admin")
    _due(here)
    _due(away)
    vm_schedules.run_due()
    assert runs == [("vm.power", "shutdown")]
    done = vm_schedules.schedule(here["id"])
    assert done["last_result"] == "succeeded" and done["next_run"] > datetime.now(UTC).isoformat()
    assert vm_schedules.schedule(away["id"])["last_run"] is None  # its own node runs it


def test_snapshots_are_kept_to_their_number(runs):
    s = vm_schedules.create(
        {"vm_name": "web", "action": "snapshot", "days": list(range(7)), "time": "01:00", "keep": 2}, "admin"
    )
    vm_schedules._store().record(
        s["id"], None, None, ["sched-old1", "sched-old2"], (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    )
    vm_schedules.run_due()
    made = vm_schedules.schedule(s["id"])["made"]
    assert len(made) == 2 and made[0] == "sched-old2" and made[1].startswith(f"sched-{s['id'][:6]}-")
    assert runs[0][0] == "vm.snapshot_create" and runs[1] == ("vm.snapshot_delete", "sched-old1")


def test_a_run_missed_by_far_is_skipped(runs):
    s = vm_schedules.create({"vm_name": "web", "action": "stop", "days": list(range(7)), "time": "03:00"}, "admin")
    _due(s, minutes_ago=120)
    vm_schedules.run_due()
    assert runs == [] and vm_schedules.schedule(s["id"])["next_run"] > datetime.now(UTC).isoformat()


def test_the_api_needs_the_right_of_each_action(client, auth_headers):
    admin = auth_headers("root")
    r = client.post(
        "/vms/web/schedules", json={"action": "snapshot", "days": [1, 3], "time": "02:00", "keep": 3}, headers=admin
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert [x["action"] for x in client.get("/vms/web/schedules", headers=admin).json()["items"]] == ["snapshot"]
    viewer = auth_headers("eve", role="viewer")
    assert (
        client.post(
            "/vms/web/schedules", json={"action": "start", "days": [1], "time": "08:00"}, headers=viewer
        ).status_code
        == 403
    )
    assert client.delete(f"/vms/other/schedules/{sid}", headers=admin).status_code == 404
    assert client.delete(f"/vms/web/schedules/{sid}", headers=admin).status_code == 204
