"""scripts/ensure-backup-apparmor.sh: QEMU's local AppArmor include gets the backup export's rule once, between
markers, the administrator's own lines kept, and a second run changes nothing."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ensure-backup-apparmor.sh"


def _run(tmp_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "HYPERLITE_APPARMOR_LOCAL": str(tmp_path / "apparmor.d" / "local" / "abstractions" / "libvirt-qemu"),
        "HYPERLITE_APPARMOR_LIBVIRT": str(tmp_path / "apparmor.d" / "libvirt"),
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=True)


def test_the_rule_is_added_once_beside_the_administrators_lines(tmp_path):
    local = tmp_path / "apparmor.d" / "local" / "abstractions" / "libvirt-qemu"
    local.parent.mkdir(parents=True)
    local.write_text("  /srv/isos/** r,\n")
    assert "may bind" in _run(tmp_path).stdout
    text = local.read_text()
    assert text.startswith("  /srv/isos/** r,\n")
    assert text.count("/run/hyperlite/backup/*.sock rw,") == 1
    assert text.count("/run/hyperlite/migration/*/*.sock rw,") == 1  # a live migration between clusters' sockets
    assert _run(tmp_path).stdout == ""  # nothing to change
    assert local.read_text() == text


def test_an_older_rule_between_the_markers_is_replaced(tmp_path):
    local = tmp_path / "apparmor.d" / "local" / "abstractions" / "libvirt-qemu"
    local.parent.mkdir(parents=True)
    local.write_text("# BEGIN hyperlite backup store\n  /old/path rw,\n# END hyperlite backup store\n  /mine r,\n")
    _run(tmp_path)
    text = local.read_text()
    assert "/old/path" not in text and "  /mine r,\n" in text and text.count("# BEGIN hyperlite backup store") == 1
    # An installation that had the backups' rule only gets the migration's too, at its next upgrade.
    assert "/run/hyperlite/migration/*/*.sock rw," in text


def test_nothing_is_done_without_apparmor(tmp_path):
    assert _run(tmp_path).stdout == ""
    assert not (tmp_path / "apparmor.d").exists()
