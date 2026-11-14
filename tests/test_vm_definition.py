"""A VM's definition exported (app/core/vm_definition.py)."""

import libvirt

from app.core import object_meta, permissions, vm_definition

XML = """<domain><name>web-1</name><os firmware='efi'><loader secure='yes'/></os><devices>
<disk type='file' device='disk'><source file='/var/lib/hyperlite/images/web-1.qcow2'/><target dev='vda'/></disk>
<disk type='file' device='cdrom'><target dev='sda'/></disk>
<interface type='network'><mac address='52:54:00:aa:bb:cc'/><source network='lan'/><vlan><tag id='20'/></vlan></interface>
</devices></domain>"""


class Pool:
    def name(self):
        return "fast"


class Vol:
    def storagePoolLookupByVolume(self):
        return Pool()


class Conn:
    def storageVolLookupByPath(self, path):
        if path.endswith("web-1.qcow2"):
            return Vol()
        raise libvirt.libvirtError("no")


class Domain:
    def name(self):
        return "web-1"

    def XMLDesc(self, flags):
        return XML

    def info(self):
        return [1, 4096 * 1024, 4096 * 1024, 2, 0]

    def blockInfo(self, dev):
        return [20 * 1024**3 + 1, 0, 0]


def test_definition_and_its_forms(database):
    pool = permissions.create_pool("web")
    permissions.add_pool_member(pool, "web-1")
    object_meta.put("vm", "web-1", "", ["prod"])
    found = vm_definition.read(Conn(), Domain())
    assert found["firmware"] == "uefi_secure" and found["vcpu"] == 2 and found["memory_mb"] == 4096
    assert found["disks"] == [{"size_gb": 21, "storage_pool": "fast", "target": "vda"}]
    assert found["interfaces"] == [{"network": "lan", "mac": "52:54:00:aa:bb:cc", "vlan_tag": 20}]
    assert found["resource_pool"] == "web" and found["tags"] == ["prod"]
    body = vm_definition.as_create_body(found)
    assert body == {"name": "web-1", "vcpu": 2, "memory_mb": 4096, "firmware": "uefi_secure",
                    "disks": [{"size_gb": 21}], "network": "lan", "storage_pool": "fast"}  # fmt: skip
    hcl = vm_definition.as_terraform(found)
    assert hcl.startswith('resource "hyperlite_vm" "web_1" {')
    assert 'resource_pool = "web"' in hcl and "vlan_tag = 20" in hcl and 'tags = ["prod"]' in hcl
