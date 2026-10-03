"""Restoring over a VM is all or nothing, from a verified backup, disk by disk by target; a live backup keeps its
overlay when merging it back failed."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import libvirt
import pytest

from app.core import backup_integrity, backups


class FakeDomain:
    def listAllCheckpoints(self):
        return []  # no replication checkpoint (app/core/checkpoints.py)

    def __init__(self, disks):
        self.disks = disks  # [(dev, path, driver type)]

    def XMLDesc(self, *_):
        parts = "".join(
            f"<disk type='file' device='disk'><driver name='qemu' type='{fmt}'/><source file='{path}'/>"
            f"<target dev='{dev}' bus='virtio'/></disk>"
            for dev, path, fmt in self.disks
        )
        return f"<domain><name>vm1</name><devices>{parts}</devices></domain>"


@pytest.fixture()
def fake_convert(monkeypatch):
    """qemu-img replaced by a copy tagged with the target format, so the test sees which format was asked."""
    calls = []

    def _convert(source, dest, fmt, task_id, base_pct, span_pct):
        calls.append((Path(source).name, Path(dest).name, fmt))
        Path(dest).write_text(f"{fmt}:{Path(source).read_text()}")

    monkeypatch.setattr(backups, "_convert", _convert)
    monkeypatch.setattr(backups, "update_task_progress", lambda *a: None)
    return calls


def _vm(tmp_path, *disks):
    vm_dir = tmp_path / "images"
    vm_dir.mkdir(exist_ok=True)
    spec = []
    for dev, fmt in disks:
        path = vm_dir / f"vm1-{dev}.img"
        path.write_text(f"old {dev}")
        spec.append((dev, str(path), fmt))
    return FakeDomain(spec), vm_dir


def _images(tmp_path, *devs):
    src = tmp_path / "backup"
    src.mkdir(exist_ok=True)
    out = []
    for dev in devs:
        (src / f"{dev}.qcow2").write_text(f"backup {dev}")
        out.append((dev, src / f"{dev}.qcow2"))
    return out


def test_disks_are_replaced_by_target_in_their_own_format(tmp_path, fake_convert):
    domain, vm_dir = _vm(tmp_path, ("vda", "qcow2"), ("vdb", "raw"))
    images = list(reversed(_images(tmp_path, "vda", "vdb")))  # order must not matter: pairing is by target
    backups._restore_overwrite(None, domain, images, "t")
    assert (vm_dir / "vm1-vda.img").read_text() == "qcow2:backup vda"
    assert (vm_dir / "vm1-vdb.img").read_text() == "raw:backup vdb"
    assert sorted(p.name for p in vm_dir.iterdir()) == ["vm1-vda.img", "vm1-vdb.img"]  # no temp or old file left


def test_a_backup_whose_disks_no_longer_match_the_vm_touches_nothing(tmp_path, fake_convert):
    domain, vm_dir = _vm(tmp_path, ("vda", "qcow2"), ("vdb", "qcow2"))
    with pytest.raises(RuntimeError, match="no longer match"):
        backups._restore_overwrite(None, domain, _images(tmp_path, "vda"), "t")
    assert (vm_dir / "vm1-vda.img").read_text() == "old vda"
    assert fake_convert == []


def test_a_failed_copy_leaves_every_original_disk_intact(tmp_path, monkeypatch, fake_convert):
    domain, vm_dir = _vm(tmp_path, ("vda", "qcow2"), ("vdb", "qcow2"))
    real = backups._convert

    def fail_on_vdb(source, dest, fmt, *rest):
        if Path(source).stem == "vdb":
            raise RuntimeError("qemu-img convert failed: No space left on device")
        real(source, dest, fmt, *rest)

    monkeypatch.setattr(backups, "_convert", fail_on_vdb)
    with pytest.raises(RuntimeError, match="No space left"):
        backups._restore_overwrite(None, domain, _images(tmp_path, "vda", "vdb"), "t")
    assert (vm_dir / "vm1-vda.img").read_text() == "old vda"
    assert (vm_dir / "vm1-vdb.img").read_text() == "old vdb"
    assert sorted(p.name for p in vm_dir.iterdir()) == ["vm1-vda.img", "vm1-vdb.img"]


def test_a_failure_while_swapping_puts_the_old_disks_back(tmp_path, monkeypatch, fake_convert):
    domain, vm_dir = _vm(tmp_path, ("vda", "qcow2"), ("vdb", "qcow2"))
    real_replace = backups.os.replace
    swaps = {"n": 0}

    def flaky_replace(src, dst):
        # Each disk takes two renames (old aside, new in): fail on the second disk's new one.
        swaps["n"] += 1
        if swaps["n"] == 4:
            raise OSError("I/O error")
        real_replace(src, dst)

    monkeypatch.setattr(backups.os, "replace", flaky_replace)
    with pytest.raises(OSError):
        backups._restore_overwrite(None, domain, _images(tmp_path, "vda", "vdb"), "t")
    assert (vm_dir / "vm1-vda.img").read_text() == "old vda"
    assert (vm_dir / "vm1-vdb.img").read_text() == "old vdb"
    assert sorted(p.name for p in vm_dir.iterdir()) == ["vm1-vda.img", "vm1-vdb.img"]


def test_images_are_paired_by_the_target_the_manifest_records(tmp_path):
    src = tmp_path / "b"
    src.mkdir()
    (src / "vda.qcow2").write_text("x")
    (src / "sdb.qcow2").write_text("y")
    (src / backup_integrity.MANIFEST).write_text(
        json.dumps(
            {
                "fichiers": [
                    {"nom": "sdb.qcow2", "role": "disque", "cible": "sdb"},
                    {"nom": "vda.qcow2", "role": "disque", "cible": "vda"},
                    {"nom": "vm-config.json", "role": "config"},
                ]
            }
        )
    )
    assert [(dev, p.name) for dev, p in backups._backup_images(src)] == [("sdb", "sdb.qcow2"), ("vda", "vda.qcow2")]


def _backup_row(database, path):
    with database.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO backups (vm_name, chemin, mode, cree_le, statut) VALUES ('vm1', ?, 'froid', 'now', 'termine')",
            (str(path),),
        )
        conn.commit()
        return cur.lastrowid


def test_a_corrupted_backup_is_refused_before_anything_is_written(database, tmp_path, monkeypatch):
    src = tmp_path / "b"
    src.mkdir()
    (src / backup_integrity.MANIFEST).write_text(
        json.dumps({"fichiers": [{"nom": "vda.qcow2", "role": "disque", "cible": "vda", "taille": 1, "sha256": "x"}]})
    )
    backup_id = _backup_row(database, src)
    with database.get_conn() as conn:
        row = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
    monkeypatch.setattr(backups, "update_task_progress", lambda *a: None)
    with pytest.raises(RuntimeError, match="integrity check"):
        backups._check_before_restore(row, "t", "alice")
    with database.get_conn() as conn:
        assert conn.execute("SELECT verification FROM backups WHERE id = ?", (backup_id,)).fetchone()[0] == (
            backup_integrity.CORRUPT
        )


def test_the_restore_endpoint_refuses_what_cannot_run_before_saying_it_started(
    client, auth_headers, database, tmp_path
):
    headers = auth_headers("alice")
    backup_id = _backup_row(database, tmp_path)
    assert client.post(f"/backups/{backup_id}/restore", json={"mode": "sideways"}, headers=headers).status_code == 422
    r = client.post(f"/backups/{backup_id}/restore", json={"mode": "new", "new_name": "bad name!"}, headers=headers)
    assert r.status_code == 422
    assert client.post("/backups/9999/restore", json={"mode": "overwrite"}, headers=headers).status_code == 404


# --- Live backup: the overlay goes only once its disk pivoted back ---


class LiveDomain:
    def __init__(self, fail_commit=()):
        self.fail_commit = set(fail_commit)

    def blockCommit(self, dev, base, top, bandwidth, flags):
        if dev in self.fail_commit:
            raise libvirt.libvirtError("block commit failed")

    def blockJobInfo(self, dev, flags):
        return {}

    def blockJobAbort(self, dev, flags):
        pass


class Snap:
    def __init__(self):
        self.deleted = False

    def delete(self, flags):
        self.deleted = True


def test_overlays_are_deleted_only_for_disks_merged_back(tmp_path, database):
    overlays = {dev: tmp_path / f"{dev}.overlay" for dev in ("vda", "vdb")}
    for p in overlays.values():
        p.write_text("writes made during the backup")
    snap = Snap()
    failures = backups._merge_overlays_back(
        LiveDomain(fail_commit={"vdb"}), "vm1", [("vda", "/a"), ("vdb", "/b")], overlays, snap
    )
    assert len(failures) == 1 and failures[0].startswith("vdb")
    assert not overlays["vda"].exists()
    assert overlays["vdb"].exists()  # still referenced by the VM's disk chain
    assert snap.deleted is False  # kept: it shows which overlay the disk still depends on


def test_a_clean_merge_removes_every_overlay_and_the_snapshot(tmp_path, database):
    overlays = {"vda": tmp_path / "vda.overlay"}
    overlays["vda"].write_text("x")
    snap = Snap()
    assert backups._merge_overlays_back(LiveDomain(), "vm1", [("vda", "/a")], overlays, snap) == []
    assert not overlays["vda"].exists() and snap.deleted


def test_the_domain_xml_formats_are_read_per_target():
    dom = FakeDomain([("vda", "/x", "qcow2"), ("sdb", "/y", "raw")])
    assert backups._disk_formats(dom) == {"vda": "qcow2", "sdb": "raw"}
    assert ET.fromstring(dom.XMLDesc())  # well-formed


class _Domain:
    def __init__(self, cdrom):
        self.cdrom = cdrom

    def XMLDesc(self, *_):
        cd = f"<disk type='file' device='cdrom'><source file='{self.cdrom}'/></disk>" if self.cdrom else ""
        return (
            "<domain><memory>1048576</memory><vcpu>1</vcpu><os><type>hvm</type></os><devices>"
            f"<disk type='file' device='disk'><source file='/i/vm1.qcow2'/></disk>{cd}"
            "<interface type='network'><source network='default'/></interface></devices></domain>"
        )


@pytest.mark.parametrize(
    ("cdrom", "expected"), [("/i/vm1-cloudinit.iso", True), ("/isos/debian.iso", False), (None, False)]
)
def test_a_backup_records_whether_the_vm_was_set_up_by_cloud_init(database, tmp_path, cdrom, expected):
    backups._write_vm_config(_Domain(cdrom), tmp_path)
    assert json.loads((tmp_path / "vm-config.json").read_text())["cloud_init"] is expected


def test_a_copy_restored_under_a_new_name_gets_a_new_cloud_init_drive(tmp_path, monkeypatch):
    """Found on a real host: the copy kept netplan's `match: macaddress` of the original, and never got an address."""
    from app.core import vm_builder

    made = []
    monkeypatch.setattr(backups, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(
        vm_builder, "create_cloudinit_reseed_iso", lambda name: made.append(name) or tmp_path / f"{name}-cloudinit.iso"
    )
    assert backups._reseed_iso({"cloud_init": True}, "vm1", "copy") == tmp_path / "copy-cloudinit.iso"
    assert backups._reseed_iso({"cloud_init": False}, "vm1", "copy") is None
    # A backup older than the flag: guessed from the original's drive, as for a clone.
    assert backups._reseed_iso({}, "vm1", "copy2") is None
    (tmp_path / "vm1-cloudinit.iso").write_text("")
    assert backups._reseed_iso({}, "vm1", "copy3") == tmp_path / "copy3-cloudinit.iso"
    assert made == ["copy", "copy3"]


def test_a_backup_records_the_os_label_and_the_ssh_user(database, tmp_path):
    """A copy restored under a new name lost them: no OS shown, and the SSH terminal did not know which account."""
    from app.core.vm_meta import set_vm_os_label, set_vm_ssh_user

    set_vm_os_label("vm1", "Debian 12")
    set_vm_ssh_user("vm1", "debian")

    class _Named(_Domain):
        def XMLDesc(self, *_):
            return super().XMLDesc().replace("<domain>", "<domain><name>vm1</name>", 1)

    backups._write_vm_config(_Named(None), tmp_path)
    config = json.loads((tmp_path / "vm-config.json").read_text())
    assert (config["os_label"], config["ssh_user"]) == ("Debian 12", "debian")
