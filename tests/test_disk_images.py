"""A disk image Hyperlite did not write is read only once it names no other file (app/core/disk_images.py,
GHSA-q6pm-r5rj-538c). Real images, made by qemu-img."""

import shutil
import subprocess

import pytest

from app.core import disk_images

pytestmark = pytest.mark.skipif(shutil.which("qemu-img") is None, reason="qemu-img is not installed")


def img(path, *args):
    subprocess.run(["qemu-img", "create", "-q", *args, str(path), "16M"], check=True)
    return path


def test_a_standalone_image_is_read_with_its_format(tmp_path):
    assert disk_images.check(img(tmp_path / "a.qcow2", "-f", "qcow2")) == "qcow2"
    assert disk_images.check(img(tmp_path / "b.raw", "-f", "raw")) == "raw"


def test_an_image_based_on_another_file_is_refused(tmp_path):
    other = tmp_path / "elsewhere" / "file"
    other.parent.mkdir()
    other.write_bytes(b"\0" * 1024 * 1024)
    image = img(tmp_path / "a.qcow2", "-f", "qcow2", "-b", str(other), "-F", "raw")
    with pytest.raises(disk_images.UnsafeImage, match="based on another file"):
        disk_images.check(image)
    with pytest.raises(disk_images.UnsafeImage):
        disk_images.check(image, within=tmp_path / "points")  # outside the directory allowed


def test_a_replication_chain_inside_its_directory_is_read(tmp_path):
    for point in ("p1", "p2"):
        (tmp_path / "vm" / point).mkdir(parents=True)
    img(tmp_path / "vm" / "p1" / "sda.qcow2", "-f", "qcow2")
    second = img(tmp_path / "vm" / "p2" / "sda.qcow2", "-f", "qcow2", "-b", "../p1/sda.qcow2", "-F", "qcow2")
    assert disk_images.check(second, within=tmp_path / "vm") == "qcow2"
    with pytest.raises(disk_images.UnsafeImage):
        disk_images.check(second)  # no backing file allowed at all


def test_an_external_data_file_is_refused(tmp_path):
    image = img(tmp_path / "a.qcow2", "-f", "qcow2", "-o", f"data_file={tmp_path / 'data'}")
    with pytest.raises(disk_images.UnsafeImage, match="external data file"):
        disk_images.check(image)


def test_a_vmdk_whose_extent_is_another_file_is_refused(tmp_path):
    image = img(tmp_path / "a.vmdk", "-f", "vmdk", "-o", "subformat=monolithicFlat")
    with pytest.raises(disk_images.UnsafeImage, match="extents"):
        disk_images.check(image)
    assert disk_images.check(img(tmp_path / "b.vmdk", "-f", "vmdk")) == "vmdk"  # one file: its extent is itself


def test_a_file_qemu_img_cannot_read_is_refused(tmp_path):
    with pytest.raises(disk_images.UnsafeImage, match="could not be read"):
        disk_images.check(tmp_path / "missing.qcow2")


def test_a_restore_refuses_a_backup_image_based_on_a_host_file(tmp_path, monkeypatch):
    from app.core import backups

    monkeypatch.setattr(backups, "update_task_progress", lambda *a: None)
    point = tmp_path / "web" / "20260101-000000"
    point.mkdir(parents=True)
    host_file = tmp_path / "host-file"
    host_file.write_bytes(b"\0" * 1024 * 1024)
    image = img(point / "sda.qcow2", "-f", "qcow2", "-b", str(host_file), "-F", "raw")
    with pytest.raises(RuntimeError, match="based on another file"):
        backups._convert(image, tmp_path / "restored.qcow2", "qcow2", "t", 0, 10)
    assert not (tmp_path / "restored.qcow2").exists()
    backups._convert(img(point / "sdb.qcow2", "-f", "qcow2"), tmp_path / "ok.qcow2", "qcow2", "t", 0, 10)
    assert (tmp_path / "ok.qcow2").exists()
