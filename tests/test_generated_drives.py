"""The drives Hyperlite generates for a VM (its cloud-init seed) are written next to its disk, on whichever pool that
is. Everything that finds them again (the Cloud-Init tab, a clone, a conversion to a template, a deletion) used to
rebuild their path from the default pool's directory: a VM made on an NFS pool had "no cloud-init drive", found by
the Terraform provider's acceptance tests."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app.core import cloudinit_edit, vm_builder


def _domain_xml(disk, *cdroms):
    drives = "".join(
        f"<disk type='file' device='cdrom'><source file='{c}'/><target dev='sd{chr(98 + i)}'/></disk>"
        for i, c in enumerate(cdroms)
    )
    return (
        f"<domain><name>web</name><devices><disk type='file' device='disk'><source file='{disk}'/>"
        f"<target dev='sda'/></disk>{drives}</devices></domain>"
    )


class _Domain:
    def __init__(self, xml):
        self.xml = xml

    def XMLDesc(self, flags=0):
        return self.xml

    def isActive(self):
        return False


@pytest.fixture()
def images(monkeypatch, tmp_path):
    monkeypatch.setattr(vm_builder, "IMAGES_DIR", tmp_path / "images")
    return tmp_path / "images"


def test_a_drive_next_to_the_vms_disk_on_another_pool_is_its_own(images):
    root = ET.fromstring(_domain_xml("/mnt/nfs/web.qcow2", "/mnt/nfs/web-cloudinit.iso"))
    assert vm_builder.cloudinit_drive(root, "web") == Path("/mnt/nfs/web-cloudinit.iso")
    domain = _Domain(_domain_xml("/mnt/nfs/web.qcow2", "/mnt/nfs/web-cloudinit.iso"))
    assert cloudinit_edit.drive_path(domain, "web") == Path("/mnt/nfs/web-cloudinit.iso")


def test_a_drive_in_the_default_directory_is_its_own_whatever_the_disks_pool(images):
    # An imported disk on a pool, its reseed drive in the default directory (create_cloudinit_reseed_iso).
    root = ET.fromstring(_domain_xml("/srv/fast/web.qcow2", images / "web-cloudinit.iso"))
    assert vm_builder.cloudinit_drive(root, "web") == images / "web-cloudinit.iso"


def test_an_image_of_the_iso_library_with_the_same_name_is_never_taken_for_it(images):
    # Rewritten or deleted by the service (root), it would be an image of the library lost.
    root = ET.fromstring(_domain_xml("/mnt/nfs/web.qcow2", "/var/lib/hyperlite/isos/web-cloudinit.iso"))
    assert vm_builder.generated_drives(root, "web") == []
    other = ET.fromstring(_domain_xml("/mnt/nfs/web.qcow2", "/mnt/nfs/db-cloudinit.iso", "/mnt/nfs/debian-13.iso"))
    assert vm_builder.generated_drives(other, "web") == []


def test_the_installation_media_of_older_vms_are_its_own_too(images):
    root = ET.fromstring(_domain_xml("/srv/fast/web.qcow2", "/srv/fast/web-oemdrv.iso", "/srv/fast/web-cloudinit.iso"))
    assert [p.name for _d, p in vm_builder.generated_drives(root, "web")] == ["web-oemdrv.iso", "web-cloudinit.iso"]
