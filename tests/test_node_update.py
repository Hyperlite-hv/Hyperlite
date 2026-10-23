"""One update per node: Hyperlite and Debian together, the reboot said beforehand (docs/design/updates-1.0.md)."""

import subprocess

import pytest

from app.core import audit, host_system
from app.core.tasks import create_task
from app.routers import update


def test_the_packages_that_need_a_reboot_are_known_from_their_names():
    names = ["linux-image-6.12.0-27-amd64", "libc6", "openssl", "qemu-system-x86", "intel-microcode", "vim"]
    assert host_system.needs_reboot(names) == ["intel-microcode", "libc6", "linux-image-6.12.0-27-amd64"]


def test_the_debian_side_of_the_check(monkeypatch):
    monkeypatch.setattr(
        host_system,
        "updates",
        lambda refresh=False: {
            "available": True,
            "packages": [
                {"name": "hyperlite", "security": False},
                {"name": "openssl", "security": True},
                {"name": "linux-image-amd64", "security": True},
                {"name": "vim", "security": False},
            ],
            "restart_required": False,
        },
    )
    s = update._system_summary()
    assert (s["packages"], s["security"], s["restart_scheduled"]) == (3, 2, ["linux-image-amd64"])
    assert "hyperlite" not in s["names"]


@pytest.fixture()
def job(database, monkeypatch, tmp_path):
    calls = {"spawned": [], "upgrade": []}
    monkeypatch.setattr(update, "_backup", lambda task_id: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(update, "_apt_update_with_retry", lambda **k: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(update, "_spawn_outside_service", lambda unit, argv: calls["spawned"].append(unit))
    monkeypatch.setattr(update.version, "STARTUP_VERSION", "1.0.0")
    monkeypatch.setattr(host_system, "REBOOT_REQUIRED", tmp_path / "reboot-required")

    def run(versions, reboot=False, system=True):
        seq = iter(versions)
        monkeypatch.setattr(update, "_dpkg_installed_version", lambda: next(seq))

        def upgrade(cmd, log):
            calls["upgrade"].append(cmd)
            if reboot:
                host_system.REBOOT_REQUIRED.write_text("*** System restart required ***")
            log("Setting up openssl ...")
            return 0

        monkeypatch.setattr(host_system, "run_upgrade", upgrade)
        task_id = create_task("hyperlite_update", "hyperlite", username="alice")
        update._run_update_job_apt(task_id, "alice", system=system)
        with database.get_conn() as conn:
            return conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]

    return run, calls


def test_only_debian_changed_hyperlite_keeps_running(job):
    run, calls = job
    assert run(["1.0.0", "1.0.0"]) == "succeeded"
    assert "full-upgrade" in calls["upgrade"][0] and calls["spawned"] == []


def test_hyperlite_changed_it_restarts_under_its_watchdog_and_a_reboot_is_said(job, database):
    run, calls = job
    assert run(["1.0.0", "1.0.1"], reboot=True) == "succeeded"
    assert calls["spawned"] == ["hyperlite-update-watchdog", "hyperlite-update-restart"]
    audit._AUDIT_QUEUE.join()  # the audit is written in the background
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'host_reboot_required'").fetchone()[0] == 1


def test_the_upgrade_command_answers_debian_prompts_and_keeps_hyperlite_from_restarting(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    cmd = update._full_upgrade_command()
    assert cmd[-1] == "full-upgrade" and "HYPERLITE_SKIP_RESTART=1" in cmd and "DEBIAN_FRONTEND=noninteractive" in cmd
    assert "Dpkg::Options::=--force-confold" in cmd


def test_hyperlite_alone_is_installed_without_a_time_limit(job, monkeypatch):
    """Hyperlite alone (system=False), as the full upgrade: apt-get runs to its end, its output in the task's log. A
    300 s limit killed it in the middle of dpkg on a loaded node (a new dependency still unpacking), leaving packages
    unpacked and not configured."""
    run, calls = job

    real_run = update.subprocess.run

    def no_timeout(cmd, *args, **kwargs):
        if "install" in cmd:  # dpkg runs under an install: never killed (a download may keep its limit)
            raise AssertionError(f"apt-get install run with a time limit: {cmd}")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(update, "_keep_previous", lambda task_id: None)
    monkeypatch.setattr(update.subprocess, "run", no_timeout)
    assert run(["1.0.0", "1.0.1"], system=False) == "succeeded"
    [cmd] = calls["upgrade"]
    assert cmd[-3:] == ["install", "--only-upgrade", "hyperlite"] and "HYPERLITE_SKIP_RESTART=1" in cmd
