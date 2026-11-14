"""Updates you can follow (docs/design/updates-2.md): the run and its steps, measured progress only, the outcome decided
by the server after the restart, the checks before starting, the version check's settings, and the notification
centre. Each scenario of the design's section 4 has its test here."""

import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from app.core import host_system, notification_feed, update_check, update_preflight, update_runs
from app.core.tasks import create_task
from app.routers import update


def _feed(username="root", admin=True):
    return notification_feed.entries(username, admin)


# ---- APT's status channel: the only source of a percentage ----


def test_apt_status_lines_are_read_and_others_left_alone():
    assert update_runs.parse_status("dlstatus:2:42.5:Retrieving file 3 of 7") == (
        "download",
        42.5,
        "Retrieving file 3 of 7",
    )
    assert update_runs.parse_status("pmstatus:openssl:80:Installing openssl") == ("install", 80.0, "Installing openssl")
    assert update_runs.parse_status("Setting up openssl (3.0.15) ...") is None


# ---- The job: steps, measured progress, failures where they happen ----


@pytest.fixture()
def job(database, monkeypatch, tmp_path):
    """The update job with the system faked: `apt` says what each apt-get call prints and returns."""
    state = {"apt": [], "spawned": [], "backup": 0, "index": 0}
    monkeypatch.setattr(update_runs, "WATCHDOG_RESULT", tmp_path / "watchdog-result.json")
    monkeypatch.setattr(
        update, "_backup", lambda task_id: state.__setitem__("backup", state["backup"] + 1) or tmp_path / "b.tar.gz"
    )
    monkeypatch.setattr(update, "_stage_watchdog", lambda: None)
    monkeypatch.setattr(update, "_keep_previous", lambda task_id, apt_opts=(): None)
    monkeypatch.setattr(update, "_spawn_outside_service", lambda unit, argv: state["spawned"].append(unit))
    monkeypatch.setattr(update.version, "STARTUP_VERSION", "1.0.0")
    monkeypatch.setattr(host_system, "REBOOT_REQUIRED", tmp_path / "reboot-required")
    monkeypatch.setattr(update_preflight, "_dpkg_audit", lambda: "")
    monkeypatch.setattr(update_preflight, "_lock_held", lambda: None)
    monkeypatch.setattr(update_preflight, "_in_cluster", lambda: False)
    index_ok = {"value": True}
    monkeypatch.setattr(
        update, "_apt_update_with_retry",
        lambda **k: subprocess.CompletedProcess([], 0 if index_ok["value"] else 100, "", "Could not resolve 'mirror'"),
    )  # fmt: skip

    def run(versions, answers, system=True, reboot=False):
        seq = iter(versions)
        monkeypatch.setattr(update, "_dpkg_installed_version", lambda: next(seq))
        replies = iter(answers)

        def upgrade(cmd, log):
            state["apt"].append(cmd)
            lines, code = next(replies)
            for line in lines:
                log(line)
            if reboot and "--download-only" not in cmd:
                host_system.REBOOT_REQUIRED.write_text("*** System restart required ***")
            return code

        monkeypatch.setattr(host_system, "run_upgrade", upgrade)
        task_id = create_task("hyperlite_update", "hyperlite", username="alice")
        run_id = update_runs.begin(task_id, "online", "1.0.0", versions[-1], system, "alice")
        update._run_update_job_apt(task_id, "alice", system=system)
        return update_runs.get(run_id)

    return run, state, index_ok


def _steps(run):
    return {s["id"]: s for s in run["steps"]}


DOWNLOAD = (["dlstatus:1:10:Retrieving 1 of 2", "dlstatus:2:100:Retrieved"], 0)
INSTALL = (
    ["pmstatus:hyperlite:35:Unpacking", "Setting up hyperlite (1.0.1) ...", "pmstatus:hyperlite:100:Installed"],
    0,
)


def test_a_new_version_is_downloaded_installed_then_verified_after_the_restart(job):
    run, state, _ = job
    r = run(["1.0.0", "1.0.1"], [DOWNLOAD, INSTALL])
    steps = _steps(r)
    assert r["status"] == "verifying" and r["to_version"] == "1.0.1"
    assert [steps[s]["status"] for s in ("preflight", "backup", "index", "download", "install")] == ["done"] * 5
    assert steps["download"]["progress"] == 100 and steps["install"]["progress"] == 100
    assert steps["backup"]["progress"] is None  # nothing measured: no invented percentage
    assert steps["restart"]["status"] == "running" and steps["verify"]["status"] == "pending"
    assert "--download-only" in state["apt"][0] and "--download-only" not in state["apt"][1]
    assert state["spawned"] == ["hyperlite-update-watchdog", "hyperlite-update-restart"]


def test_debian_only_ends_without_a_restart_and_says_a_reboot_is_needed(job):
    run, state, _ = job
    r = run(["1.0.0", "1.0.0"], [DOWNLOAD, INSTALL], reboot=True)
    assert r["status"] == "succeeded" and r["reboot_required"] and state["spawned"] == []
    assert _steps(r)["restart"]["status"] == "skipped"
    kinds = [e["kind"] for e in _feed()]
    assert "reboot_required" in kinds and "update_done" in kinds
    pinned = [e for e in _feed() if e["pinned"]]
    assert [e["kind"] for e in pinned] == ["reboot_required"]


def test_the_network_down_stops_at_the_index_and_installs_nothing(job):
    run, state, index_ok = job
    index_ok["value"] = False
    r = run(["1.0.0", "1.0.1"], [])
    assert r["status"] == "failed" and "apt-get update failed" in r["error"]
    assert _steps(r)["index"]["status"] == "failed" and _steps(r)["download"]["status"] == "pending"
    assert state["apt"] == []
    assert _feed()[0]["kind"] == "update_failed"


def test_a_cut_download_installs_nothing(job):
    run, state, _ = job
    r = run(["1.0.0", "1.0.1"], [(["dlstatus:1:40:Retrieving 1 of 2", "E: Failed to fetch ... Connection reset"], 100)])
    steps = _steps(r)
    assert r["status"] == "failed" and steps["download"]["status"] == "failed" and steps["download"]["progress"] == 40
    assert steps["install"]["status"] == "pending" and len(state["apt"]) == 1 and state["spawned"] == []


def test_a_failed_install_is_said_with_its_step(job):
    run, _, _ = job
    r = run(["1.0.0", "1.0.1"], [DOWNLOAD, (["pmstatus:libc6:20:Unpacking", "dpkg: error processing"], 1)])
    assert r["status"] == "failed" and _steps(r)["install"]["status"] == "failed" and "exited with 1" in r["error"]


def test_a_blocking_check_stops_before_the_backup(job, monkeypatch):
    run, state, _ = job
    monkeypatch.setattr(update_preflight, "_dpkg_audit", lambda: "The following packages are only half configured")
    r = run(["1.0.0", "1.0.1"], [])
    assert r["status"] == "failed" and _steps(r)["preflight"]["status"] == "failed" and state["backup"] == 0


# ---- The outcome, decided at the next start from the watchdog's verdict ----


@pytest.fixture()
def verifying(database, monkeypatch, tmp_path):
    monkeypatch.setattr(update_runs, "WATCHDOG_RESULT", tmp_path / "watchdog-result.json")
    run_id = update_runs.begin("t-1", "online", "1.0.0", "1.0.1", True, "alice")
    update_runs.verifying(run_id)
    return run_id, tmp_path / "watchdog-result.json"


def test_the_new_version_answering_closes_the_run_as_succeeded(verifying):
    _ = verifying
    closed = update_runs.finalize_after_restart("1.0.1")
    assert closed["status"] == "succeeded" and closed["duration_s"] is not None
    assert {s["id"]: s["status"] for s in closed["steps"]}["verify"] == "done"
    assert update_runs.current() is None
    assert _feed()[0]["kind"] == "update_done"


def test_the_watchdog_putting_the_old_version_back_is_a_rollback(verifying):
    _, result = verifying
    result.write_text(json.dumps({"outcome": "rolled_back", "version": "", "detail": "1.0.1 did not answer"}))
    closed = update_runs.finalize_after_restart("1.0.0")
    assert closed["status"] == "rolled_back" and "did not answer" in closed["error"]
    assert _feed()[0]["kind"] == "update_rolled_back"


def test_the_old_process_answering_before_the_new_one_decides_nothing(verifying):
    assert update_runs.finalize_after_restart("1.0.0") is None
    assert update_runs.current()["status"] == "verifying"


def test_no_verdict_after_the_window_is_a_failure(verifying):
    late = datetime.now(UTC) + update_runs.VERIFY_TIMEOUT + timedelta(minutes=1)
    assert update_runs.finalize_after_restart("1.0.0", now=late)["status"] == "failed"


# ---- The API: reload, double start, rights, checks ----


@pytest.fixture()
def apt_node(database, monkeypatch, tmp_path):
    monkeypatch.setattr(update, "_install_method", lambda: "apt")
    monkeypatch.setattr(update, "_dpkg_installed_version", lambda: "1.0.0")
    monkeypatch.setattr(update, "_candidate", lambda: "1.0.1")
    monkeypatch.setattr(update, "_previous", lambda: None)
    monkeypatch.setattr(update.host_system, "channel", lambda: "stable")
    monkeypatch.setattr(update_preflight, "_dpkg_audit", lambda: "")
    monkeypatch.setattr(update_preflight, "_lock_held", lambda: None)
    monkeypatch.setattr(update_preflight, "_in_cluster", lambda: False)
    monkeypatch.setattr(update_preflight, "_compare", lambda a, b: 1 if a > b else (0 if a == b else -1))
    started = []
    monkeypatch.setattr(update, "_start_job", lambda user, kind, task_id, system=True: started.append(kind))
    return started


def test_a_reload_finds_the_update_in_progress(client, auth_headers, apt_node):
    admin = auth_headers("root")
    r = client.post("/update/apply", json={"system": True}, headers=admin)
    assert r.status_code == 202, r.text
    status = client.get("/update/status", headers=admin).json()  # what a reloaded page reads
    assert status["current"]["to_version"] == "1.0.1" and status["current"]["status"] == "running"
    run = client.get(f"/update/runs/{status['current']['id']}", headers=admin).json()
    assert [s["id"] for s in run["steps"]][:3] == ["preflight", "backup", "index"] and isinstance(run["log"], list)
    assert _feed()[0]["kind"] == "update_started"


def test_a_second_start_is_refused(client, auth_headers, apt_node, monkeypatch):
    monkeypatch.setattr(update, "_update_in_progress", lambda: "alice")
    r = client.post("/update/apply", json={"system": True}, headers=auth_headers("root"))
    assert r.status_code == 409 and "alice" in r.json()["detail"] and apt_node == []


def test_a_blocking_check_refuses_the_start_with_the_checks(client, auth_headers, apt_node, monkeypatch):
    monkeypatch.setattr(update_preflight, "_lock_held", lambda: "/var/lib/dpkg/lock-frontend")
    r = client.post("/update/apply", json={"system": True}, headers=auth_headers("root"))
    assert r.status_code == 409 and apt_node == []
    checks = {c["id"]: c["status"] for c in r.json()["detail"]["checks"]}
    assert checks["apt_lock"] == "blocking" and checks["dpkg"] == "ok"


def test_an_older_candidate_blocks_and_a_cluster_only_warns(apt_node, monkeypatch):
    monkeypatch.setattr(update_preflight, "_in_cluster", lambda: True)
    checks = {c["id"]: c["status"] for c in update_preflight.run("1.2.0", "1.1.0")}
    assert checks["version"] == "blocking" and checks["cluster"] == "warning"
    checks = {c["id"]: c["status"] for c in update_preflight.run("1.2.0", "2.0.0")}
    assert checks["version"] == "warning"


def test_a_viewer_reads_and_starts_nothing(client, auth_headers, apt_node):
    viewer = auth_headers("eve", role="viewer")
    for method, path in (("get", "/update/status"), ("get", "/update/history"), ("get", "/update/preflight"),
                         ("post", "/update/apply"), ("put", "/update/settings")):  # fmt: skip
        r = getattr(client, method)(path, headers=viewer, **({"json": {}} if method != "get" else {}))
        assert r.status_code == 403, path
    assert apt_node == []


def test_the_check_interval_is_chosen(client, auth_headers, apt_node):
    admin = auth_headers("root")
    assert client.get("/update/settings", headers=admin).json()["check_interval_h"] == update_check.DEFAULT_INTERVAL_H
    assert client.put("/update/settings", json={"check_interval_h": 24}, headers=admin).json()["check_interval_h"] == 24
    assert client.put("/update/settings", json={"check_interval_h": 5}, headers=admin).status_code == 422
    now = datetime.now(UTC)
    assert update_check.due(now, 24, None)
    assert not update_check.due(now, 24, (now - timedelta(hours=2)).isoformat())
    assert not update_check.due(now, 0, None)  # off


# ---- The version check: available, critical, up to date ----


def test_a_version_with_security_fixes_is_critical_and_pinned_until_installed(database):
    update_check.remember({"verifiable": True, "up_to_date": False, "commit_local": "1.0.0", "remote_commit": "1.0.1",
                           "system": {"packages": 3, "security": 1}})  # fmt: skip
    [entry] = [e for e in _feed() if e["kind"].startswith("update_")]
    assert entry["kind"] == "update_critical" and entry["level"] == "error" and entry["pinned"]
    assert update_check.last_result()["critical"] is True
    update_check.remember({"verifiable": True, "up_to_date": False, "commit_local": "1.0.0", "remote_commit": "1.0.1"})
    assert len([e for e in _feed() if e["kind"].startswith("update_")]) == 1  # said once
    update_check.remember({"verifiable": True, "up_to_date": True, "commit_local": "1.0.1", "remote_commit": "1.0.1"})
    assert not any(e["pinned"] for e in _feed())


def test_a_release_note_with_a_security_section_is_critical():
    assert update_check.is_critical({"notes": [{"text": "## 1.0.1\n\n### Security\n\n- fixed"}]})
    assert not update_check.is_critical({"notes": [{"text": "### Added\n- a page"}], "system": {"security": 0}})


def test_no_update_says_so_and_nothing_is_announced(database):
    s = update_check.remember(
        {"verifiable": True, "up_to_date": True, "commit_local": "1.0.0", "remote_commit": "1.0.0"}
    )
    assert s["up_to_date"] and _feed() == []


def test_an_unreachable_mirror_is_kept_as_the_last_result(database):
    s = update_check.remember({"verifiable": False, "error": "Unable to contact the APT repository: timed out"})
    assert not s["verifiable"] and "timed out" in update_check.last_result()["error"]


# ---- The notification centre ----


def test_read_unread_archive_and_audience(database):
    a = notification_feed.add("error", "vm_crashed", "VM web crashed", subject="web")
    b = notification_feed.add("update", "update_available", "1.0.1", audience="admin")
    assert notification_feed.unread_count("eve", admin=False) == 1  # the admin entry is not hers
    assert notification_feed.unread_count("root", admin=True) == 2
    notification_feed.mark_read("root", True, [a])
    assert notification_feed.unread_count("root", True) == 1 and notification_feed.unread_count("eve", False) == 1
    notification_feed.mark_read("root", True)
    assert notification_feed.unread_count("root", True) == 0
    notification_feed.archive("root", True, [b])
    assert [e["id"] for e in notification_feed.entries("root", True)] == [a]
    assert [e["id"] for e in notification_feed.entries("root", True, archived=True)] == [b]


def test_an_alert_stays_pinned_until_it_resolves(database):
    notification_feed.from_journal("alert_rule_fired", "db (hv-02)", "failed", "Processor: cpu_pct = 95 % > 85 %")
    notification_feed.from_journal("alert_rule_fired", "db (hv-02)", "failed", "Processor: cpu_pct = 96 % > 85 %")
    [entry] = _feed()
    assert entry["pinned"] and entry["kind"] == "alert_fired"
    notification_feed.from_journal("alert_rule_resolved", "db (hv-02)", "success", "Processor: back under 85 %")
    assert not _feed()[0]["pinned"]


def test_the_feed_api(client, auth_headers, database):
    notification_feed.add("error", "vm_crashed", "VM web crashed", subject="web", link="vm:web:summary")
    headers = auth_headers("eve", role="viewer")
    body = client.get("/notifications/feed", headers=headers).json()
    assert body["unread"] == 1 and body["items"][0]["link"] == "vm:web:summary"
    assert client.post("/notifications/feed/read", json={"ids": None}, headers=headers).json() == {"unread": 0}
    assert client.post("/notifications/feed/archive", json={"ids": []}, headers=headers).status_code == 422
