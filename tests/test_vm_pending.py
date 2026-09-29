"""Changes to a running VM's definition that wait for its next start."""

import libvirt

from app.core import vm_pending

LIVE = """<domain type='kvm' id='3'><name>web</name><memory unit='KiB'>2097152</memory><currentMemory unit='KiB'>1048576</currentMemory>
<vcpu placement='static'>2</vcpu><os><type arch='x86_64' machine='pc-q35-8.2'>hvm</type><loader readonly='yes' type='pflash'>/x</loader></os>
<cpu mode='host-passthrough'><topology sockets='1' dies='1' cores='2' threads='1'/></cpu>
<devices>
<disk type='file' device='disk'><driver name='qemu' type='qcow2' cache='none'/><source file='/var/lib/libvirt/images/web.qcow2' index='1'/>
<target dev='vda' bus='virtio'/><boot order='1'/><alias name='virtio-disk0'/><address type='pci' slot='0x04'/></disk>
<interface type='network'><mac address='52:54:00:00:00:01'/><source network='default' portid='abc' bridge='virbr0'/><target dev='vnet3'/>
<model type='virtio'/><alias name='net0'/></interface>
<graphics type='vnc' port='5901' autoport='yes' listen='127.0.0.1'/><memballoon model='virtio'><alias name='balloon0'/></memballoon>
</devices></domain>"""


def _next(**repl):
    xml = (
        LIVE.replace(" id='3'", "")
        .replace(" port='5901'", "")
        .replace("<alias name='net0'/>", "")
        .replace("<target dev='vnet3'/>", "")
    )
    for old, new in repl.items():
        xml = xml.replace(old.replace("_", " "), new)
    return xml


def test_runtime_details_are_not_changes():
    assert vm_pending.diff(LIVE, _next()) == []


def test_changes_are_listed_with_their_running_and_next_values():
    nxt = LIVE.replace("<vcpu placement='static'>2</vcpu>", "<vcpu placement='static'>4</vcpu>")
    nxt = nxt.replace("<memory unit='KiB'>2097152</memory>", "<memory unit='KiB'>4194304</memory>")
    nxt = nxt.replace("cache='none'", "cache='writeback'").replace("pc-q35-8.2", "pc-q35-9.0")
    nxt = nxt.replace(
        "</devices>",
        "<interface type='bridge'><mac address='52:54:00:00:00:02'/><source bridge='br0'/><vlan><tag id='20'/></vlan><model type='e1000'/></interface>"
        "<hostdev mode='subsystem' type='pci'><source><address domain='0x0000' bus='0x03' slot='0x00' function='0x0'/></source></hostdev></devices>",
    )
    changes = {(c["cle"], c["objet"]): (c["actuel"], c["prochain"]) for c in vm_pending.diff(LIVE, nxt)}
    assert changes == {
        ("vcpu", None): ("2", "4"),
        ("memoire", None): ("2048 MiB", "4096 MiB"),
        ("machine", None): ("pc-q35-8.2", "pc-q35-9.0"),
        ("disque", "vda"): (
            "/var/lib/libvirt/images/web.qcow2 · virtio · type=qcow2, cache=none",
            "/var/lib/libvirt/images/web.qcow2 · virtio · type=qcow2, cache=writeback",
        ),
        ("interface", "52:54:00:00:00:02"): (None, "br0 · e1000 · VLAN 20"),
        ("passthrough", "pci 0x0000:0x03:0x00:0x0"): (None, "pci 0x0000:0x03:0x00:0x0"),
    }


def test_removed_device_and_boot_order():
    nxt = LIVE.replace("<boot order='1'/>", "")
    nxt = nxt[: nxt.index("<interface")] + nxt[nxt.index("</interface>") + len("</interface>") :]
    nxt = nxt.replace("<os>", "<os><boot dev='network'/>")
    changes = {(c["cle"], c["objet"]): (c["actuel"], c["prochain"]) for c in vm_pending.diff(LIVE, nxt)}
    assert changes == {
        ("demarrage", None): ("vda", "network"),
        ("interface", "52:54:00:00:00:01"): ("default · virtio", None),
    }


class _Dom:
    def __init__(self, active=True, nxt=None):
        self.active, self.nxt = active, nxt or LIVE

    def isActive(self):
        return 1 if self.active else 0

    def isPersistent(self):
        return 1

    def XMLDesc(self, flags=0):
        return self.nxt if flags & libvirt.VIR_DOMAIN_XML_INACTIVE else LIVE


class _Conn:
    def __init__(self, dom):
        self.dom = dom

    def lookupByName(self, name):
        if name != "web":
            raise libvirt.libvirtError("no domain")
        return self.dom

    def close(self):
        pass


def test_api_reports_pending_changes_of_a_running_vm(client, auth_headers, monkeypatch):
    from app.routers.vms import settings

    conn = _Conn(_Dom(nxt=LIVE.replace(">2</vcpu>", ">3</vcpu>")))
    opened = []
    monkeypatch.setattr(settings, "open_conn", lambda node=None: opened.append(node) or conn)
    admin = auth_headers("root", "admin")
    r = client.get("/vms/web/pending-changes?node=n2", headers=admin).json()
    assert r == {"en_marche": True, "changements": [{"cle": "vcpu", "objet": None, "actuel": "2", "prochain": "3"}]}
    assert opened == ["n2"]
    conn.dom.active = False
    assert client.get("/vms/web/pending-changes", headers=admin).json() == {"en_marche": False, "changements": []}
    assert client.get("/vms/nope/pending-changes", headers=admin).status_code == 404
