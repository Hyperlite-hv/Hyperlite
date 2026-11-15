"""Patch plans, VM group autoscaling, triggers and the script library (docs/design/console-2.md, sections 4 to 7)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core import guest_scripts, patch_plans, triggers, vm_group_scaling

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)  # a Tuesday, 03:00 in Paris


# ---- Patch plans ----


def _plan(**over):
    return {"name": "prod", "target_kind": "vms", "target": ["web", "db"], "days": [1], "at_time": "03:00",
            "tz": "Europe/Paris", "duration_min": 120, "reboot": True, **over}  # fmt: skip


def test_a_plan_is_checked():
    for bad in ({"days": [], "monthday": None}, {"days": [1], "monthday": 3}, {"at_time": "25:00"},
                {"tz": "Mars/Olympus"}, {"duration_min": 10}, {"target_kind": "cluster"}, {"days": [7]}):  # fmt: skip
        with pytest.raises(patch_plans.PlanError):
            patch_plans.validate(_plan(**bad))


def test_occurrences_follow_the_time_zone():
    p = patch_plans.validate(_plan())
    nxt = patch_plans.occurrences(p, NOW - timedelta(minutes=1), 2)
    assert nxt[0] == NOW and nxt[1] == NOW + timedelta(days=7)
    # Summer time: 03:00 in Paris is 01:00 UTC.
    summer = patch_plans.occurrences(p, datetime(2026, 6, 1, tzinfo=UTC), 1)[0]
    assert summer.hour == 1 and summer.weekday() == 1
    last = patch_plans.validate(_plan(days=[], monthday=-1))
    assert patch_plans.occurrences(last, datetime(2026, 2, 1, tzinfo=UTC), 1)[0].day == 28


def test_a_plan_schedules_one_run_per_occurrence(database, make_user, monkeypatch):
    from app.core import guest_updates

    make_user("ann")
    monkeypatch.setattr(patch_plans, "_now", lambda: NOW - timedelta(hours=1))
    plan = patch_plans.save(_plan(), "ann")
    assert plan["next"][0] == NOW.isoformat()
    runs = []
    monkeypatch.setattr(patch_plans, "_local_running", lambda: {"web", "db", "other"})
    monkeypatch.setattr(
        guest_updates, "schedule", lambda vms, a, b, reboot, by: runs.append((vms, a, b, reboot, by)) or {"id": "r1"}
    )
    patch_plans.tick(NOW - timedelta(minutes=5))  # not yet
    patch_plans.tick(NOW + timedelta(minutes=1))
    patch_plans.tick(NOW + timedelta(minutes=2))  # the same occurrence: once
    assert runs == [(["db", "web"], NOW.isoformat(), (NOW + timedelta(hours=2)).isoformat(), True, "plan:prod")]


def test_a_plan_acts_with_its_authors_rights(database, make_user, monkeypatch):
    from app.core import guest_updates, permissions

    make_user("eve", role="viewer")
    monkeypatch.setattr(patch_plans, "_now", lambda: NOW - timedelta(hours=1))
    patch_plans.save(_plan(), "eve")
    monkeypatch.setattr(patch_plans, "_local_running", lambda: {"web", "db"})
    monkeypatch.setattr(permissions, "has_privilege", lambda user, vm, p: vm == "web")
    runs = []
    monkeypatch.setattr(guest_updates, "schedule", lambda vms, *a: runs.append(vms) or {"id": "r"})
    patch_plans.tick(NOW + timedelta(minutes=1))
    assert runs == [["web"]]


def test_plans_through_the_api(client, auth_headers):
    admin = auth_headers("root2")
    r = client.post("/guest-updates/plans", json=_plan(), headers=admin)
    assert r.status_code == 201, r.text
    assert client.post("/guest-updates/plans", json=_plan(at_time="9h"), headers=admin).status_code == 422
    assert len(client.get("/guest-updates/plans", headers=admin).json()["items"]) == 1
    assert client.delete(f"/guest-updates/plans/{r.json()['id']}", headers=admin).status_code == 200


# ---- Autoscaling ----


def _settings(**over):
    return {**vm_group_scaling.validate({"min_count": 1, "max_count": 4, "up_pct": 70, "down_pct": 20, "window_min": 10,
                                         "cooldown_min": 15, "step": 2}), "last_action_at": None, **over}  # fmt: skip


def test_scaling_settings_are_checked():
    for bad in ({"min_count": 3, "max_count": 2}, {"up_pct": 30, "down_pct": 25}, {"window_min": 1}, {"step": 0}):
        with pytest.raises(vm_group_scaling.ScalingError):
            vm_group_scaling.validate({"min_count": 1, "max_count": 3, "up_pct": 75, "down_pct": 25, **bad})


def test_average_cpu_needs_the_whole_window():
    def s(name, minutes, cpu):
        return {"target": name, "ts": (NOW - timedelta(minutes=minutes)).isoformat(), "cpu_pct": cpu}

    full = [s("g-1", m, 80) for m in range(10)] + [s("g-2", m, 90) for m in range(10)] + [s("other", 1, 0)]
    assert vm_group_scaling.average_cpu({"g-1", "g-2"}, 10, NOW, full) == 85
    assert vm_group_scaling.average_cpu({"g-1"}, 10, NOW, [s("g-1", 2, 99)]) is None


def test_decisions():
    members = [(1, "g-1", True), (2, "g-2", True)]
    d = vm_group_scaling.decide
    assert d({}, _settings(), members, 90, NOW)[0] == ("up", 2)
    assert d({}, _settings(max_count=3), members, 90, NOW)[0] == ("up", 1)
    assert d({}, _settings(), members, 10, NOW)[0] == ("down", "g-2")
    assert d({}, _settings(), members, 50, NOW)[0] is None
    assert d({}, _settings(), members, None, NOW)[0] is None
    recent = (NOW - timedelta(minutes=5)).isoformat()
    assert d({}, _settings(last_action_at=recent), members, 90, NOW) == (None, "cool-down")
    # The bounds come first, cool-down or not.
    assert d({}, _settings(min_count=3, last_action_at=recent), members, 50, NOW)[0] == ("up", 1)
    stopped_first = [(1, "g-1", True), (2, "g-2", True), (3, "g-3", False)]
    assert d({}, _settings(max_count=2), stopped_first, 50, NOW)[0] == ("down", "g-3")


# ---- Triggers ----


@pytest.fixture()
def job(database):
    from app.core import jobs

    return jobs._store().create_job("restart", "", "admin", NOW.isoformat(), [
        {"target_type": "each_target", "target": "", "command": "systemctl restart app", "condition_type": "exit_code",
         "condition_value": "0"}])  # fmt: skip


def test_journal_entries_become_events():
    assert triggers.event_of("alert_rule_fired", "db (hv-1)", "failed", "cpu: cpu_pct = 95") == (
        "alert_fired",
        ["db"],
        "cpu",
    )
    assert triggers.event_of("alert_rule_fired", "db (hv-1)", "failed", "cpu: x (silenced)") is None
    assert triggers.event_of("vm_crashed", "web", "failed", "it crashed") == ("vm_crashed", ["web"], None)
    assert triggers.event_of("backup_vm", "web", "success", "") is None
    assert triggers.event_of("guest_updates", "a, b", "failed", "failed on b") == ("guest_update_failed", ["b"], None)
    assert triggers.event_of("start_vm", "web", "success", "") is None


def test_a_trigger_starts_its_job_once_per_cool_down(job, monkeypatch):
    from app.core import jobs

    started = []
    monkeypatch.setattr(
        jobs, "start_job_run", lambda job_id, targets, dry, by: started.append((job_id, targets, by)) or "run-1"
    )
    t = triggers.save(
        {"name": "heal", "event": "alert_fired", "filter": "cpu", "job_id": job, "cooldown_min": 30}, "admin"
    )
    assert triggers.on_journal("alert_rule_fired", "db (hv-1)", "failed", "mem: high", NOW) == []  # another rule
    first = triggers.on_journal("alert_rule_fired", "db (hv-1)", "failed", "cpu: high", NOW)
    assert first[0]["run_id"] == "run-1" and started == [(job, ["db"], "trigger:heal")]
    again = triggers.on_journal("alert_rule_fired", "web (hv-1)", "failed", "cpu: high", NOW + timedelta(minutes=5))
    assert again[0]["outcome"] == "skipped: cool-down" and len(started) == 1
    triggers.on_journal("alert_rule_fired", "web (hv-1)", "failed", "cpu: high", NOW + timedelta(minutes=31))
    assert len(started) == 2
    assert triggers.triggers()[0]["last_firing"]["subject"] == "web"
    assert [f["outcome"] for f in triggers.firings(t["id"])][-1] == "started"


def test_a_trigger_names_a_known_job_and_event(database):
    with pytest.raises(triggers.TriggerError):
        triggers.save({"name": "x", "event": "vm_crashed", "job_id": 999}, "admin")
    with pytest.raises(triggers.TriggerError):
        triggers.save({"name": "x", "event": "disk_full", "job_id": 1}, "admin")


def test_the_journal_hands_entries_to_triggers(job, monkeypatch):
    import threading

    from app.core import audit

    seen = threading.Event()
    monkeypatch.setattr(triggers, "on_journal", lambda *a: seen.set())
    audit.log_action("system", "vm_crashed", "web", "failed", "it crashed")
    assert seen.wait(5)


# ---- Script library ----


def test_parameters_are_checked_and_handed_as_variables():
    s = guest_scripts.get("builtin:timezone")
    assert guest_scripts.values_for(s, {}) == {"TZ_NAME": "Europe/Paris"}
    with pytest.raises(guest_scripts.ScriptError):
        guest_scripts.values_for(s, {"TZ_NAME": "Europe/Paris; rm -rf /"})
    assert guest_scripts.program(s, {"TZ_NAME": "UTC"}).startswith("export TZ_NAME='UTC'\n")
    win = guest_scripts.get("builtin:win_rdp")
    assert guest_scripts.program({**win, "params": []}, {"A": "b"}).startswith("$env:A = 'b'\n")


def test_start_and_status_commands():
    cmd = guest_scripts.start_command("sh", "rabc", "echo hi")
    assert (
        "base64 -d > /var/lib/hyperlite-scripts/rabc.sh" in cmd
        and "echo $? > /var/lib/hyperlite-scripts/rabc.rc" in cmd
    )
    assert "FromBase64String" in guest_scripts.start_command("powershell", "rabc", "'hi'")
    assert guest_scripts.parse_status("0\n|\nall good\n") == (0, "all good")
    assert guest_scripts.parse_status("|\n") == (None, "")


def test_a_run_starts_in_each_guest_and_is_followed(database, monkeypatch):
    from app.core import guest_updates

    calls = []
    monkeypatch.setattr(guest_updates, "_shell", lambda vm: "powershell" if vm == "win" else "sh")
    monkeypatch.setattr(
        guest_updates,
        "_exec",
        lambda vm, shell, script, user="system", timeout_s=110: (
            calls.append((vm, user, script)) or {"stdout": "0\n|\ndone\n"}
        ),
    )
    run = guest_scripts.run("builtin:free_disk", ["web", "win"], {}, "ann")
    assert run["results"]["win"]["state"] == "skipped" and run["results"]["web"]["state"] == "running"
    assert calls[0][1] == "ann"  # started as the account that asked
    guest_scripts.tick()
    after = guest_scripts.runs()[0]
    assert after["status"] == "done" and after["results"]["web"] == {
        **after["results"]["web"],
        "state": "done",
        "exit": 0,
        "output": "done",
    }


def test_custom_scripts_through_the_api(client, auth_headers):
    admin = auth_headers("root2")
    body = {"name": "Hello", "shell": "sh", "body": "echo $WHO", "params": [{"name": "WHO", "default": "world"}]}
    r = client.post("/guest-scripts", json=body, headers=admin)
    assert r.status_code == 201, r.text
    bad = {**body, "params": [{"name": "lower"}]}
    assert client.post("/guest-scripts", json=bad, headers=admin).status_code == 422
    names = [s["name"] for s in client.get("/guest-scripts", headers=admin).json()["items"]]
    assert "Install Docker" in names and "Hello" in names
    assert client.put("/guest-scripts/builtin:docker", json=body, headers=admin).status_code == 409
    viewer = auth_headers("eve", role="viewer")
    r = client.post("/guest-scripts/runs", json={"script_id": "builtin:free_disk", "vms": ["web"]}, headers=viewer)
    assert r.status_code == 403


def test_a_plan_made_by_root_of_the_node_runs(database, monkeypatch):
    from app.core import guest_updates, security, self_node

    root = security.local_root_name(self_node.name())
    monkeypatch.setattr(patch_plans, "_now", lambda: NOW - timedelta(hours=1))
    patch_plans.save(_plan(), root)
    monkeypatch.setattr(patch_plans, "_local_running", lambda: {"web"})
    runs = []
    monkeypatch.setattr(guest_updates, "schedule", lambda vms, *a: runs.append(vms) or {"id": "r"})
    patch_plans.tick(NOW + timedelta(minutes=1))
    assert runs == [["web"]]
