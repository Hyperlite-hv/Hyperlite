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
    (tmp_path / "root/hyperlite/app").mkdir()  # the code: not moved, the package owns it
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
    assert (old_layout / "root/hyperlite/app").is_dir()


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
