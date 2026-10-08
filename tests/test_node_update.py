"""One update per node: Hyperlite and Debian together, the reboot said beforehand (docs/design/updates-1.0.md)."""

import subprocess

import pytest

from app.core import host_system
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
            "disponible": True,
            "paquets": [
                {"nom": "hyperlite", "securite": False},
                {"nom": "openssl", "securite": True},
                {"nom": "linux-image-amd64", "securite": True},
                {"nom": "vim", "securite": False},
            ],
            "redemarrage_requis": False,
        },
    )
    s = update._system_summary()
    assert (s["paquets"], s["securite"], s["redemarrage_prevu"]) == (3, 2, ["linux-image-amd64"])
    assert "hyperlite" not in s["noms"]


@pytest.fixture()
def job(database, monkeypatch, tmp_path):
    calls = {"spawned": [], "upgrade": []}
    monkeypatch.setattr(update, "_backup", lambda task_id: tmp_path / "backup.tar.gz")
    monkeypatch.setattr(update, "_apt_update_with_retry", lambda **k: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(update, "_spawn_outside_service", lambda unit, argv: calls["spawned"].append(unit))
    monkeypatch.setattr(update.version, "STARTUP_VERSION", "1.0.0")
    monkeypatch.setattr(host_system, "REBOOT_REQUIRED", tmp_path / "reboot-required")

    def run(versions, reboot=False):
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
        update._run_update_job_apt(task_id, "alice")
        with database.get_conn() as conn:
            return conn.execute("SELECT statut FROM tasks WHERE id = ?", (task_id,)).fetchone()[0]

    return run, calls


def test_only_debian_changed_hyperlite_keeps_running(job):
    run, calls = job
    assert run(["1.0.0", "1.0.0"]) == "termine"
    assert "full-upgrade" in calls["upgrade"][0] and calls["spawned"] == []


def test_hyperlite_changed_it_restarts_under_its_watchdog_and_a_reboot_is_said(job, database):
    run, calls = job
    assert run(["1.0.0", "1.0.1"], reboot=True) == "termine"
    assert calls["spawned"] == ["hyperlite-update-watchdog", "hyperlite-update-restart"]
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'host_reboot_required'").fetchone()[0] == 1


def test_the_upgrade_command_answers_debian_prompts_and_keeps_hyperlite_from_restarting(monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    cmd = update._full_upgrade_command()
    assert cmd[-1] == "full-upgrade" and "HYPERLITE_SKIP_RESTART=1" in cmd and "DEBIAN_FRONTEND=noninteractive" in cmd
    assert "Dpkg::Options::=--force-confold" in cmd
