"""What a code review of 1.28.1 found (ECC's review agents in a sandbox, each finding checked against the code), one
test per fix, where the module's own tests did not already hold it."""

import xml.etree.ElementTree as ET

import pytest

from app.core import disk_images, linked_disks, recycle_bin


def _disk(driver=None):
    xml = "<disk type='file' device='disk'>"
    if driver is not None:
        xml += f"<driver name='qemu' type='{driver}'/>"
    return ET.fromstring(xml + "<source file='/x'/></disk>")


def test_a_copy_reads_a_disk_in_the_format_its_definition_names():
    assert disk_images.declared_format(_disk("qcow2")) == "qcow2"
    assert disk_images.declared_format(_disk("raw")) == "raw"
    assert disk_images.declared_format(_disk()) == "raw", "no driver type: QEMU reads it raw, so does the copy"
    with pytest.raises(disk_images.UnsafeImage):
        disk_images.declared_format(_disk("vmdk"))


def test_a_base_image_or_a_binned_disk_is_never_given_to_a_vm(monkeypatch):
    monkeypatch.setattr(linked_disks, "dependents", lambda path, conn: ["web1", "web2"] if path == "/p/base" else [])
    monkeypatch.setattr(recycle_bin, "binned_paths", lambda: {"/p/old.qcow2"})
    assert "web1, web2" in linked_disks.refusal("/p/base", None)
    assert "recycle bin" in linked_disks.refusal("/p/old.qcow2", None)
    assert linked_disks.refusal("/p/free.qcow2", None) is None


def test_the_host_network_routes_go_to_the_member_named_by_node():
    """The dashboard sends ?node= to manage another member's interfaces: without the forwarding, the node the browser
    is connected to applied the change to its own network, and reported it as the other's."""
    from app.core import cluster_view
    from app.routers.host_network import router

    assert any(d.dependency is cluster_view.on_node for d in router.dependencies)


def test_a_backup_time_is_one_directory_name_never_a_path(tmp_path):
    from app.core import backup_store

    for bad in ("../../../../etc/cron.d", "a/b", "..", "", "x" * 41, None):
        with pytest.raises(ValueError):
            backup_store.check_stamp(bad)
    for good in ("2026-11-16T10:00:00Z", "2026-11-16T10:00:00+00:00"):
        assert backup_store.check_stamp(good) == good


def test_a_remotes_bad_backup_entries_are_skipped_not_used_as_paths(monkeypatch):
    from app.core import backup_remotes

    vm = "6f1c2a3b-0000-4000-8000-000000000001"
    good = {"vm_uuid": vm, "time": "2026-11-16T10:00:00Z", "manifest": {"disks": {"vda": {}}, "files": {}}}
    export = {
        "config": {"id": "s", "chunk_size": 4194304},
        "backups": [
            good,
            {**good, "time": "../../../../etc/cron.d"},
            {**good, "vm_uuid": "not-a-uuid"},
            {**good, "manifest": {"disks": {}, "files": {"../x": {}}}},
        ],
    }

    class Client:
        def json(self, path):
            return export

    monkeypatch.setattr(backup_remotes, "get", lambda name: {})
    monkeypatch.setattr(backup_remotes, "_client", lambda remote: Client())
    store = backup_remotes.RemoteStore("far", "s")
    assert store.backups() == [(vm, "2026-11-16T10:00:00Z", good["manifest"])]


def test_a_live_restore_never_reads_through_an_image_that_names_another_file(tmp_path, monkeypatch):
    from app.core import live_restore

    image = tmp_path / "point" / "vda.qcow2"
    image.parent.mkdir()
    image.write_bytes(b"x")

    def unsafe(path, within=None):
        raise disk_images.UnsafeImage("based on another file")

    monkeypatch.setattr(disk_images, "check", unsafe)
    monkeypatch.setattr(live_restore.subprocess, "run", lambda *a, **k: pytest.fail("overlay made"))
    with pytest.raises(live_restore.LiveRestoreError):
        live_restore.overlay(image, tmp_path / "new.qcow2")


def test_a_source_backup_dated_in_the_future_never_ranks_in_the_copy():
    from datetime import UTC, datetime, timedelta

    from app.core import backup_store_sync

    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    assert backup_store_sync._plausible({"time": "2026-11-16T11:00:00+00:00"}, "s", now)
    assert not backup_store_sync._plausible({"time": (now + timedelta(days=30)).isoformat()}, "s", now)
    assert not backup_store_sync._plausible({"time": "garbage"}, "s", now)


def test_an_encrypted_store_whose_directory_says_otherwise_is_refused():
    from app.core import backup_stores

    class Plain:
        encrypted = False

    with pytest.raises(backup_stores.StoreError, match="recorded as encrypted"):
        backup_stores._refuse_downgrade(Plain(), {"name": "main", "encrypted": 1})
    backup_stores._refuse_downgrade(Plain(), {"name": "main", "encrypted": 0})
    backup_stores._refuse_downgrade(Plain(), None)
