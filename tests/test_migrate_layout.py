"""scripts/migrate_layout.sh: the move between /root/hyperlite and the layout modelled on Proxmox VE, in both
directions, all or nothing (docs/design/privilege-separation.md, section 9)."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_layout.sh"
# (where an item is today, where it goes)
PAIRS = [
    ("root/hyperlite/hyperlite.db", "var/lib/hyperlite/hyperlite.db"),
    ("root/hyperlite/hyperlite.db.before-v1", "var/lib/hyperlite/hyperlite.db.before-v1"),
    ("root/hyperlite/data/isos/debian.iso", "var/lib/hyperlite/template/iso/debian.iso"),
    ("root/hyperlite/data/templates/web.qcow2", "var/lib/hyperlite/template/vm/web.qcow2"),
    ("root/hyperlite/data/backups/vm1.tar", "var/lib/hyperlite/dump/vm1.tar"),
    ("root/hyperlite/data/vm-exports/vm1.ova", "var/lib/hyperlite/dump/export/vm1.ova"),
    ("root/hyperlite-container-backups/ct1.tar", "var/lib/hyperlite/dump/container/ct1.tar"),
    ("root/hyperlite/data/imported-disks/d.vmdk", "var/lib/hyperlite/import/d.vmdk"),
    ("root/hyperlite/data/k3s/k3s", "var/lib/hyperlite/k3s/k3s"),
    ("root/hyperlite/data/tls/hyperlite.key", "etc/hyperlite/local/hyperlite.key"),
    ("root/hyperlite/.env", "etc/hyperlite/priv/env"),
    ("root/hyperlite/data/ssh/hyperlite_cluster", "etc/hyperlite/priv/ssh/hyperlite_cluster"),
    ("root/hyperlite/data/initial-admin-password.txt", "etc/hyperlite/priv/initial-admin-password.txt"),
    ("root/hyperlite-backups/b.tar.gz", "var/lib/hyperlite/update-backups/b.tar.gz"),
    ("root/hyperlite/data/something-else/x", "var/lib/hyperlite/legacy-data/something-else/x"),
]


def run(root, direction):
    return subprocess.run(
        ["bash", str(SCRIPT), direction],
        env={**os.environ, "HYPERLITE_LAYOUT_ROOT": str(root)},
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def old_layout(tmp_path):
    for old, _ in PAIRS:
        p = tmp_path / old
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(old)
    (tmp_path / "root/hyperlite/data/casper-cache/ubuntu").mkdir(parents=True)
    (tmp_path / "root/hyperlite/data/config-copy").mkdir()  # empty here...
    (tmp_path / "var/lib/hyperlite/config-copy").mkdir(parents=True)  # ...and there: not a conflict
    (tmp_path / "root/hyperlite/app").mkdir()
    (tmp_path / "root/hyperlite/app/main.py").write_text("the code of 1.0.2: not moved, its package owns it")
    return tmp_path


def test_forward_puts_every_item_where_proxmox_would_and_keeps_its_content(old_layout):
    r = run(old_layout, "forward")
    assert r.returncode == 0, r.stdout + r.stderr
    for old, new in PAIRS:
        assert not (old_layout / old).exists(), old
        assert (old_layout / new).read_text() == old
    assert not (old_layout / "root/hyperlite/data").exists()
    assert not (old_layout / "var/lib/hyperlite/legacy-data/casper-cache").exists()  # dropped, not carried along
    for d in ("etc/hyperlite/priv", "etc/hyperlite/priv/ssh", "etc/hyperlite/local"):
        assert (old_layout / d).stat().st_mode & 0o777 == 0o700, d
    assert (old_layout / "root/hyperlite/app/main.py").is_file()
    assert (old_layout / "var/lib/hyperlite/hyperlite.db").stat().st_mode & 0o777 == 0o600


def test_it_is_idempotent_and_goes_back(old_layout):
    assert run(old_layout, "forward").returncode == 0
    again = run(old_layout, "forward")
    assert again.returncode == 0 and "nothing to move" in again.stdout
    back = run(old_layout, "back")
    assert back.returncode == 0, back.stdout + back.stderr
    for old, _ in PAIRS:
        assert (old_layout / old).read_text() == old, old


def test_an_item_on_both_sides_stops_it_before_anything_moves(old_layout):
    (old_layout / "var/lib/hyperlite/hyperlite.db").write_text("another database")
    r = run(old_layout, "forward")
    assert r.returncode == 1 and "both" in r.stdout
    for old, _ in PAIRS:
        assert (old_layout / old).exists(), old  # nothing moved
    assert (old_layout / "var/lib/hyperlite/hyperlite.db").read_text() == "another database"


def test_a_failure_halfway_puts_back_what_was_moved(old_layout):
    (old_layout / "etc").mkdir()
    (old_layout / "etc/hyperlite").write_text("a file where a directory is needed")  # the TLS and secrets cannot move
    r = run(old_layout, "forward")
    assert r.returncode == 1 and "goes back" in r.stdout
    for old, _ in PAIRS:
        assert (old_layout / old).read_text() == old, old  # the database and the libraries moved, then came back
    assert not (old_layout / "var/lib/hyperlite/template").exists()


def test_a_wrong_direction_is_refused(tmp_path):
    assert run(tmp_path, "sideways").returncode == 2


def test_the_configuration_a_node_receives_is_never_touched(old_layout):
    """/var/lib/hyperlite/config-copy is where a node receives a controller's configuration, in both layouts; the
    controller's own staging (data/config-copy) moves to config-copy-staging (app/core/config_copy.py)."""
    received = old_layout / "var/lib/hyperlite/config-copy/hyperlite.db"
    received.write_text("received")
    assert run(old_layout, "forward").returncode == 0
    assert received.read_text() == "received"
    assert (old_layout / "var/lib/hyperlite/config-copy-staging").is_dir()
    assert run(old_layout, "back").returncode == 0
    assert received.read_text() == "received"
    assert (old_layout / "root/hyperlite/data/config-copy").is_dir()


def test_going_back_leaves_no_empty_directory_of_the_new_layout(old_layout):
    assert run(old_layout, "forward").returncode == 0
    (old_layout / "var/lib/hyperlite/previous").mkdir()  # lives there in both layouts: stays
    assert run(old_layout, "back").returncode == 0
    for d in ("var/lib/hyperlite/template", "var/lib/hyperlite/dump", "etc/hyperlite"):
        assert not (old_layout / d).exists(), d
    assert (old_layout / "var/lib/hyperlite/previous").is_dir()


def test_what_the_old_installation_leaves_is_removed_once_its_code_is_gone(old_layout):
    """1.1's package removed the code of /root/hyperlite before its first start: the Python environment and the
    bytecode it leaves go, then the directory; a file of the administrator's stays, named in the log."""
    old = old_layout / "root/hyperlite"
    (old / "app/main.py").unlink()
    (old / "app/__pycache__").mkdir()
    (old / "app/__pycache__/main.cpython-313.pyc").write_text("bytecode")
    (old / "venv/bin").mkdir(parents=True)
    (old / "venv/bin/python3").write_text("python")
    r = run(old_layout, "forward")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not old.exists() and "removed" in r.stdout
    old.mkdir()
    (old / "notes.txt").write_text("the administrator's")
    r = run(old_layout, "forward")
    assert (old / "notes.txt").exists() and "notes.txt" in r.stdout


def test_a_node_already_moved_starts_whatever_is_found_at_the_old_place(old_layout):
    """A VM migrated from a node still on 1.0 brings its ISO image to the same path, under /root/hyperlite: on a node
    already moved, that is no reason to keep the service from starting, and the image stays where the VM reads it."""
    assert run(old_layout, "forward").returncode == 0
    stray = old_layout / "root/hyperlite/data/isos/debian.iso"
    stray.parent.mkdir(parents=True)
    stray.write_text("copied by a migration")
    r = run(old_layout, "forward")
    assert r.returncode == 0 and "WARNING" in r.stdout, r.stdout
    assert stray.read_text() == "copied by a migration"
    moved = old_layout / "var/lib/hyperlite/template/iso/debian.iso"
    assert moved.read_text() == "root/hyperlite/data/isos/debian.iso"


def test_a_move_cut_short_is_finished_at_the_next_start(old_layout):
    """The database moves first: a power cut after it leaves the rest at the old place, moved at the next start."""
    (old_layout / "var/lib/hyperlite").mkdir(parents=True, exist_ok=True)
    for name in ("hyperlite.db", "hyperlite.db.before-v1"):
        (old_layout / "root/hyperlite" / name).rename(old_layout / "var/lib/hyperlite" / name)
    r = run(old_layout, "forward")
    assert r.returncode == 0, r.stdout + r.stderr
    for old, new in PAIRS:
        assert (old_layout / new).read_text() == old, new


FAKE_VIRSH = """#!/bin/bash
D="$(dirname "$0")/virsh"
echo "$*" >> "$D/calls"
case "$1" in
    list) printf 'web\\ndb\\nother\\n' ;;
    dumpxml) [ "$2 $3" = "--inactive --security-info" ] || exit 1; cat "$D/$5.xml" ;;
    define) n=$(grep -o '<name>[^<]*' "$2" | cut -c7-); cp "$2" "$D/$n.defined" ;;
esac
"""


def test_the_vms_read_their_iso_image_from_its_new_place(old_layout):
    """A VM's definition names its ISO image by its path: it follows the move. A VM whose image is in neither place is
    left alone; the definition is read with its secrets, or writing it back would drop the console's password."""
    old_iso = old_layout / "root/hyperlite/data/isos"
    virsh_dir = old_layout / "virsh"
    virsh_dir.mkdir()
    for vm, iso in (("web", "debian.iso"), ("db", "debian.iso"), ("other", "none.iso")):
        (virsh_dir / f"{vm}.xml").write_text(
            f"<domain><name>{vm}</name><graphics passwd='secret'/>"
            f"<disk device='cdrom'><source file='{old_iso}/{iso}'/></disk></domain>\n"
        )
    fake = old_layout / "fake-virsh"
    fake.write_text(FAKE_VIRSH)
    fake.chmod(0o755)
    r = subprocess.run(
        ["bash", str(SCRIPT), "forward"],
        env={**os.environ, "HYPERLITE_LAYOUT_ROOT": str(old_layout), "HYPERLITE_LAYOUT_VIRSH": str(fake)},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    new_iso = old_layout / "var/lib/hyperlite/template/iso"
    for vm in ("web", "db"):
        defined = (virsh_dir / f"{vm}.defined").read_text()
        assert f"file='{new_iso}/debian.iso'" in defined and "passwd='secret'" in defined
    assert not (virsh_dir / "other.defined").exists()


def test_the_hosts_libvirt_is_never_touched_by_the_tests(old_layout):
    r = run(old_layout, "forward")
    assert r.returncode == 0 and "VM " not in r.stdout
