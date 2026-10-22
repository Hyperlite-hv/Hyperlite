"""Linked disks (app/core/linked_disks.py): the backing chain read from the qcow2 header, as qemu-img reads it, and the
VMs that depend on a template's disk. Two linked clones of one template were also run at the same time on libvirt
10.0 and QEMU 8.2: each read the template's data and kept its own writes, and the template stayed unchanged."""

import json
import shutil
import struct
import subprocess

import pytest

from app.core import linked_disks

qemu_img = pytest.mark.skipif(shutil.which("qemu-img") is None, reason="needs qemu-img")


def _qcow2(path, backing=None):
    """A qcow2 header as the format's specification lays it out: magic, version, backing file offset and length, and
    the backing file's name right after the header. Enough for what the module reads."""
    header = bytearray(512)
    header[0:4] = b"QFI\xfb"
    header[4:8] = struct.pack(">I", 3)
    if backing:
        name = backing.encode()
        header[8:20] = struct.pack(">QI", 104, len(name))
        header[104 : 104 + len(name)] = name
    path.write_bytes(bytes(header))
    return path


def test_the_backing_file_is_read_from_the_header(tmp_path):
    base = _qcow2(tmp_path / "base.qcow2")
    absolute = _qcow2(tmp_path / "a.qcow2", str(base))
    (tmp_path / "sub").mkdir()
    relative = _qcow2(tmp_path / "sub" / "r.qcow2", "../base.qcow2")
    assert linked_disks.backing_file(base) is None
    assert linked_disks.backing_file(absolute) == str(base)
    assert linked_disks.backing_file(relative) == str(base), "relative to the image's own directory, as QEMU reads it"


def test_anything_but_a_qcow2_image_has_no_backing_file(tmp_path):
    raw = tmp_path / "disk.raw"
    raw.write_bytes(b"\0" * 1024)
    assert linked_disks.backing_file(raw) is None
    assert linked_disks.backing_file(tmp_path / "missing.qcow2") is None
    short = tmp_path / "short.qcow2"
    short.write_bytes(b"QFI\xfb")
    assert linked_disks.backing_file(short) is None


def test_the_chain_follows_every_level_and_stops_on_a_loop(tmp_path):
    base = _qcow2(tmp_path / "base.qcow2")
    mid = _qcow2(tmp_path / "mid.qcow2", str(base))
    top = _qcow2(tmp_path / "top.qcow2", str(mid))
    assert linked_disks.chain(top) == [str(top), str(mid), str(base)]
    assert linked_disks.depends_on(top, base) and linked_disks.depends_on(top, mid)
    assert not linked_disks.depends_on(base, top) and not linked_disks.depends_on(top, top)
    a = _qcow2(tmp_path / "a.qcow2", str(tmp_path / "b.qcow2"))
    _qcow2(tmp_path / "b.qcow2", str(a))
    assert len(linked_disks.chain(a)) == 2


class _Dom:
    def __init__(self, name, *files):
        self._name = name
        disks = "".join(f"<disk type='file' device='disk'><source file='{f}'/></disk>" for f in files)
        cdrom = "<disk type='file' device='cdrom'><source file='/isos/x.iso'/></disk>"
        self._xml = f"<domain><name>{name}</name><devices>{disks}{cdrom}</devices></domain>"

    def name(self):
        return self._name

    def XMLDesc(self, flags=0):
        return self._xml


class _Conn:
    def __init__(self, *doms):
        self.doms = doms

    def listAllDomains(self):
        return list(self.doms)


def test_the_vms_reading_through_a_template_are_named(tmp_path):
    tpl = _qcow2(tmp_path / "tpl.qcow2")
    other = _qcow2(tmp_path / "other.qcow2")
    conn = _Conn(
        _Dom("web-2", str(_qcow2(tmp_path / "web-2.qcow2", str(tpl)))),
        _Dom("web-1", str(tmp_path / "data.qcow2"), str(_qcow2(tmp_path / "web-1.qcow2", str(tpl)))),
        _Dom("db", str(_qcow2(tmp_path / "db.qcow2", str(other)))),
        _Dom("full", str(_qcow2(tmp_path / "full.qcow2"))),
    )
    assert linked_disks.dependents(tpl, conn) == ["web-1", "web-2"]
    assert linked_disks.dependents(tmp_path / "unused.qcow2", conn) == []


@qemu_img
def test_a_linked_disk_reads_through_the_template_as_qemu_img_says(tmp_path):
    tpl = tmp_path / "tpl.qcow2"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(tpl), "64M"], check=True)
    clone = tmp_path / "clone.qcow2"
    linked_disks.create_linked(tpl, clone)
    info = subprocess.run(
        ["qemu-img", "info", "--backing-chain", "--output=json", str(clone)], check=True, capture_output=True, text=True
    ).stdout
    assert [i["filename"] for i in json.loads(info)] == linked_disks.chain(clone) == [str(clone), str(tpl)]
    assert json.loads(info)[0]["virtual-size"] == 64 * 1024**2
    assert clone.stat().st_size < tpl.stat().st_size * 2, "the clone holds only its own changes"


@qemu_img
def test_a_linked_disk_needs_a_qcow2_template_and_a_free_name(tmp_path):
    raw = tmp_path / "tpl.raw"
    raw.write_bytes(b"\0" * 1024 * 1024)
    with pytest.raises(linked_disks.LinkedDiskError, match="not a qcow2 image"):
        linked_disks.create_linked(raw, tmp_path / "c.qcow2")
    tpl = tmp_path / "tpl.qcow2"
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(tpl), "16M"], check=True)
    taken = tmp_path / "taken.qcow2"
    taken.write_bytes(b"mine")
    with pytest.raises(linked_disks.LinkedDiskError, match="already exists"):
        linked_disks.create_linked(tpl, taken)
    assert taken.read_bytes() == b"mine"
