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
