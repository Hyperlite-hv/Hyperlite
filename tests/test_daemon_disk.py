"""The disks' root work in hyperlite-daemon, part 1: this node's libraries, the ISOs and the disks to import
(app/core/disk_library.py, app/daemon/ops/disk.py, docs/design/privilege-separation.md, section 13). An upload goes
through the spool the API owns: the daemon takes a spool file's name, never a path, and only a regular file of its
own, so the API cannot have a link to another file stored in a library."""

import os
import time

import pytest

from app.core import disk_library, privileged
from app.daemon.registry import DaemonError


class _Host:
    """This node's libvirt: one VM with debian.iso in its CD-ROM drive."""

    def __init__(self, iso):
        self.iso = iso

    def listAllDomains(self, flags=0):
        iso = self.iso

        class Dom:
            def XMLDesc(self, flags=0):
                return f"<domain><devices><disk device='cdrom'><source file='{iso}'/></disk></devices></domain>"

        return [Dom()]

    def listAllStoragePools(self, flags=0):
        return []


@pytest.fixture()
def library(tmp_path, monkeypatch, daemon_libvirt):
    for name in ("isos", "import", "spool"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(disk_library, "ISOS", tmp_path / "isos")
    monkeypatch.setattr(disk_library, "IMPORTS", tmp_path / "import")
    monkeypatch.setattr(disk_library, "SPOOL", tmp_path / "spool")
    monkeypatch.setattr(disk_library, "STAGING", tmp_path / "staging")
    (tmp_path / "isos" / "debian.iso").write_bytes(b"iso")
    daemon_libvirt(_Host(tmp_path / "isos" / "debian.iso"))
    return tmp_path


def _upload(content=b"data"):
    name, path = disk_library.spool_file()
    path.write_bytes(content)
    return name, path


def test_an_upload_is_moved_into_its_library_readable_by_qemu(library):
    name, path = _upload(b"x" * 1000)
    assert privileged.run("disk.library_store", library="iso", spool_name=name, name="alpine.iso") == {"size": 1000}
    stored = library / "isos" / "alpine.iso"
    assert stored.read_bytes() == b"x" * 1000 and oct(stored.stat().st_mode & 0o777) == "0o644"
    assert not path.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_a_spool_entry_that_is_no_file_of_its_own_is_refused(library, kind):
    secret = library / "secret"
    secret.write_text("root's")
    name = "upload-" + "0" * 32 + ".part"
    entry = library / "spool" / name
    if kind == "symlink":
        entry.symlink_to(secret)
    elif kind == "hardlink":
        os.link(secret, entry)
    else:
        entry.mkdir()
    with pytest.raises(DaemonError) as e:
        privileged.run("disk.library_store", library="iso", spool_name=name, name="stolen.iso")
    assert e.value.kind == "refused"
    assert not (library / "isos" / "stolen.iso").exists()


@pytest.mark.parametrize(
    "spool_name, name",
    [
        ("../../etc/shadow", "x.iso"),
        ("upload-" + "0" * 32 + ".part", "../x.iso"),
        ("upload-" + "0" * 32 + ".part", "debian.iso;rm.iso"),
        ("upload-" + "0" * 32 + ".part", "notes.txt"),
    ],
)
def test_names_are_checked_again_by_the_daemon(library, spool_name, name):
    (library / "spool" / ("upload-" + "0" * 32 + ".part")).write_bytes(b"x")
    with pytest.raises(DaemonError) as e:
        privileged.run("disk.library_store", library="iso", spool_name=spool_name, name=name)
    assert e.value.kind == "refused"


def test_an_iso_never_replaces_another_and_a_disk_to_import_does(library):
    name, _ = _upload(b"new")
    with pytest.raises(DaemonError) as e:
        privileged.run("disk.library_store", library="iso", spool_name=name, name="debian.iso")
    assert e.value.kind == "refused" and (library / "isos" / "debian.iso").read_bytes() == b"iso"
    assert list((library / "spool").iterdir()) == []  # a refused upload is gone: the API cannot reach the staging
    name, _ = _upload(b"new")
    (library / "import" / "web.qcow2").write_bytes(b"old")
    privileged.run("disk.library_store", library="import", spool_name=name, name="web.qcow2", replace=True)
    assert (library / "import" / "web.qcow2").read_bytes() == b"new"


def test_an_iso_in_a_cdrom_drive_is_kept_whatever_the_api_checked(library):
    for op, args in [
        ("disk.library_rename", {"name": "debian.iso", "new_name": "other.iso"}),
        ("disk.library_delete", {"library": "iso", "name": "debian.iso"}),
    ]:
        with pytest.raises(DaemonError) as e:
            privileged.run(op, **args)
        assert e.value.kind == "in_use", op
    assert (library / "isos" / "debian.iso").exists()


def test_the_spool_keeps_a_day(library):
    _, old = _upload()
    _, recent = _upload()
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    assert disk_library.clean_spool() == 1
    assert not old.exists() and recent.exists()


def test_an_upload_through_the_api_leaves_nothing_in_the_spool(library, client, auth_headers):
    admin = auth_headers("root")
    r = client.post("/isos", files={"file": ("alpine.iso", b"abc", "application/octet-stream")}, headers=admin)
    assert r.status_code == 201, r.text
    assert (library / "isos" / "alpine.iso").read_bytes() == b"abc"
    r = client.post("/isos", files={"file": ("alpine.iso", b"abc", "application/octet-stream")}, headers=admin)
    assert r.status_code == 409
    r = client.post("/vm-disks", files={"file": ("web.qcow2", b"disk", "application/octet-stream")}, headers=admin)
    assert r.status_code == 201, r.text
    assert (library / "import" / "web.qcow2").read_bytes() == b"disk"
    assert list((library / "spool").iterdir()) == []


def test_an_ova_is_named_never_given_as_a_path(library):
    for ova in ("../../etc/passwd.ova", "disk.qcow2"):
        with pytest.raises(DaemonError) as e:
            privileged.run("disk.ova_extract", ova=ova, stem="x")
        assert e.value.kind == "refused", ova


def test_an_upload_swapped_for_a_link_after_it_is_staged_never_reaches_the_library(library, monkeypatch):
    """The check runs on the staged entry, out of the API's reach: what the spool holds after the move is ignored."""
    secret = library / "secret"
    secret.write_text("root's")
    name, path = _upload(b"x")
    rename = os.rename

    def swap_then_rename(src, dst):
        rename(src, dst)
        if str(src) == str(path):  # the API swaps the spool entry for a link right after the move
            path.symlink_to(secret)

    monkeypatch.setattr(os, "rename", swap_then_rename)
    privileged.run("disk.library_store", library="iso", spool_name=name, name="alpine.iso")
    stored = library / "isos" / "alpine.iso"
    assert not stored.is_symlink() and stored.read_bytes() == b"x"
    assert list((library / "staging").iterdir()) == []
