"""Debian's security updates every night at a time chosen per node, never a reboot, never Hyperlite itself; a
pending reboot is notified once per boot (docs/design/updates-1.0.md)."""

import pytest

from app.core import host_system, update_check


@pytest.fixture()
def apt(tmp_path, monkeypatch):
    runs = []
    monkeypatch.setattr(host_system, "AUTO_CONF", tmp_path / "apt.conf.d" / "52hyperlite")
    monkeypatch.setattr(
        host_system,
        "AUTO_TIMERS",
        {
            "apt-daily.timer": tmp_path / "apt-daily.timer.d" / "hyperlite.conf",
            "apt-daily-upgrade.timer": tmp_path / "apt-daily-upgrade.timer.d" / "hyperlite.conf",
        },
    )
    monkeypatch.setattr(host_system, "_run", lambda argv, **k: runs.append(argv))
    return runs, tmp_path


def test_on_by_default_at_half_past_three_security_only(apt):
    runs, _tmp = apt
    host_system.ensure_auto_updates_default()
    conf = host_system.AUTO_CONF.read_text()
    assert 'APT::Periodic::Unattended-Upgrade "1";' in conf and 'Automatic-Reboot "false"' in conf
    assert "label=Debian-Security" in conf and '"hyperlite"' in conf
    assert conf.index("#clear Unattended-Upgrade::Origins-Pattern;") < conf.index("Origins-Pattern {")
    assert "OnCalendar=*-*-* 03:30" in host_system.AUTO_TIMERS["apt-daily-upgrade.timer"].read_text()
    assert "OnCalendar=*-*-* 03:00" in host_system.AUTO_TIMERS["apt-daily.timer"].read_text()  # lists first
    assert host_system.auto_updates()["actif"] is True and host_system.auto_updates()["heure"] == "03:30"
    assert ["systemctl", "daemon-reload"] in runs
    # A node that chose is left alone.
    host_system.set_auto_updates(False, "00:15")
    host_system.ensure_auto_updates_default()
    assert host_system.auto_updates()["actif"] is False and host_system.auto_updates()["heure"] == "00:15"
    assert "OnCalendar=*-*-* 23:45" in host_system.AUTO_TIMERS["apt-daily.timer"].read_text()  # across midnight


def test_the_api(apt, client, auth_headers):
    admin = auth_headers("root")
    r = client.put("/host/system/auto-updates", json={"actif": True, "heure": "02:10"}, headers=admin)
    assert r.status_code == 200 and r.json()["heure"] == "02:10"
    assert (
        client.put("/host/system/auto-updates", json={"actif": True, "heure": "25:00"}, headers=admin).status_code
        == 422
    )
    assert client.get("/host/system/auto-updates", headers=auth_headers("eve", role="observateur")).status_code == 403


def test_a_pending_reboot_is_notified_once_per_boot(database, tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr(update_check, "log_action", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(host_system, "REBOOT_REQUIRED", tmp_path / "reboot-required")
    monkeypatch.setattr(host_system, "REBOOT_PKGS", tmp_path / "reboot-required.pkgs")
    monkeypatch.setattr(update_check, "BOOT_ID", tmp_path / "boot_id")
    (tmp_path / "boot_id").write_text("boot-1\n")
    assert update_check.check_reboot_needed() is False  # nothing pending
    (tmp_path / "reboot-required").write_text("*** System restart required ***")
    (tmp_path / "reboot-required.pkgs").write_text("linux-image-6.12.0-27-amd64\nlibc6\n")
    assert update_check.check_reboot_needed() is True
    assert update_check.check_reboot_needed() is False  # once per boot
    assert logged[0][1] == "host_reboot_required" and "libc6" in logged[0][4]
    (tmp_path / "boot_id").write_text("boot-2\n")  # rebooted, and again an update needs one
    assert update_check.check_reboot_needed() is True
