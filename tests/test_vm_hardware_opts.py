"""Advanced hardware settings: what is refused, what the definition becomes, what applies live."""

import xml.etree.ElementTree as ET

import libvirt
import pytest

from app.core import vm_hardware_opts as opts

XML = """<domain type='kvm'><name>web</name><memory unit='KiB'>2097152</memory>
<currentMemory unit='KiB'>2097152</currentMemory><vcpu>2</vcpu>
<os><type arch='x86_64' machine='pc-q35-6.2'>hvm</type><boot dev='hd'/></os><devices>
<disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='/v/web.qcow2'/><target dev='vda' bus='virtio'/><boot order='1'/></disk>
<disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='/v/data.qcow2'/><target dev='sda' bus='sata'/></disk>
<disk type='file' device='cdrom'><driver name='qemu' type='raw'/><target dev='sdz' bus='sata'/><readonly/><boot order='2'/></disk>
<interface type='network'><mac address='52:54:00:00:00:01'/><source network='default'/></interface>
<memballoon model='virtio'><address type='pci' slot='0x05'/></memballoon>
</devices></domain>"""

CAPS = """<capabilities><guest><arch name='x86_64'>
<machine canonical='pc-q35-9.0'>q35</machine><machine>pc-q35-6.2</machine><machine canonical='pc-i440fx-9.0'>pc</machine>
</arch></guest></capabilities>"""


class Domain:
    def __init__(self, conn, active=False):
        self.conn, self.active, self.iotune, self.memory = conn, active, None, None

    def XMLDesc(self, flags=0):
        return self.conn.xml

    def isActive(self):
        return self.active

    def setBlockIoTune(self, dev, params, flags):
        self.iotune = (dev, params)

    def setMemoryFlags(self, kib, flags):
        self.memory = kib


class Conn:
    def __init__(self, active=False):
        self.xml = XML
        self.domain = Domain(self, active)

    def defineXML(self, xml):
        ET.fromstring(xml)  # well formed
        self.xml = xml

    def getCapabilities(self):
        return CAPS


def test_the_current_settings_are_read_from_the_definition():
    d = opts.describe(Conn().domain)
    assert [x["cible"] for x in d["disques"]] == ["vda", "sda", "sdz"]
    assert d["ordre_demarrage"] == ["vda", "sdz"]
    assert d["ballooning"] == {"actif": True, "memoire_mo": 2048, "minimum_mo": 2048}
    assert d["machine"] == "pc-q35-6.2"


def test_disk_options_are_written_and_limits_apply_live():
    conn = Conn(active=True)
    r = opts.set_disk_options(
        conn,
        conn.domain,
        "vda",
        {"cache": "none", "discard": "unmap", "io": "native", "iothread": True, "iops": 500, "mbps": 50},
    )
    disk = opts.describe(conn.domain)["disques"][0]
    assert disk == {
        "cible": "vda",
        "type": "disk",
        "bus": "virtio",
        "cache": "none",
        "discard": "unmap",
        "io": "native",
        "iothread": True,
        "iops": 500,
        "mbps": 50,
    }
    assert ET.fromstring(conn.xml).findtext("iothreads") == "1"
    assert conn.domain.iotune == ("vda", {"total_iops_sec": 500, "total_bytes_sec": 50 * 1024**2})
    assert r == {"a_redemarrer": True}  # cache, discard, I/O mode and thread wait for the next start
    r = opts.set_disk_options(
        conn, conn.domain, "vda", {"cache": "none", "discard": "unmap", "io": "native", "iothread": True, "iops": 0}
    )
    assert r == {"a_redemarrer": False} and opts.describe(conn.domain)["disques"][0]["iops"] is None


@pytest.mark.parametrize(
    ("dev", "values", "message"),
    [
        ("vda", {"cache": "turbo"}, "Unknown cache mode"),
        ("vda", {"io": "native", "cache": "writeback"}, "native I/O mode needs"),
        ("sda", {"iothread": True}, "only applies to a virtio disk"),
        ("sdz", {"cache": "none"}, "CD-ROM"),
        ("vdq", {}, "No disk vdq"),
        ("vda", {"iops": -1}, "between 0"),
    ],
)
def test_bad_disk_options_are_refused(dev, values, message):
    conn = Conn()
    with pytest.raises(opts.OptionError, match=message):
        opts.set_disk_options(conn, conn.domain, dev, values)
    assert conn.xml == XML


def test_the_boot_order_is_per_device_and_drops_the_os_list():
    conn = Conn()
    opts.set_boot_order(conn, conn.domain, ["net:52:54:00:00:00:01", "sda"])
    assert opts.describe(conn.domain)["ordre_demarrage"] == ["net:52:54:00:00:00:01", "sda"]
    assert ET.fromstring(conn.xml).find("./os/boot") is None  # libvirt refuses both kinds together
    for bad in ([], ["vda", "vda"], ["nope"]):
        with pytest.raises(opts.OptionError):
            opts.set_boot_order(conn, conn.domain, bad)


def test_ballooning_minimum_and_removal():
    conn = Conn(active=True)
    assert opts.set_balloon(conn, conn.domain, True, 1024) == {"a_redemarrer": False}
    assert conn.domain.memory == 1024 * 1024  # applied live: the balloon is already there
    assert opts.describe(conn.domain)["ballooning"]["minimum_mo"] == 1024
    with pytest.raises(opts.OptionError):
        opts.set_balloon(conn, conn.domain, True, 4096)  # above the VM's memory
    assert opts.set_balloon(conn, conn.domain, False) == {"a_redemarrer": True}
    assert opts.describe(conn.domain)["ballooning"] == {"actif": False, "memoire_mo": 2048, "minimum_mo": 2048}


def test_only_the_family_default_machine_is_offered_on_a_stopped_vm():
    conn = Conn()
    assert opts.newer_machines(conn, "pc-q35-6.2") == ["pc-q35-9.0"]
    assert opts.newer_machines(conn, "pc-q35-9.0") == []
    assert opts.newer_machines(conn, "pc-i440fx-noble") == ["pc-i440fx-9.0"]
    with pytest.raises(opts.OptionError):
        opts.set_machine(conn, conn.domain, "pc-i440fx-9.0")  # another family
    opts.set_machine(conn, conn.domain, "pc-q35-9.0")
    assert opts.describe(conn.domain)["machine"] == "pc-q35-9.0"
    running = Conn(active=True)
    with pytest.raises(opts.OptionError, match="Stop the VM"):
        opts.set_machine(running, running.domain, "pc-q35-9.0")


def test_the_api_applies_under_the_vm_lock_with_the_right_privileges(client, auth_headers, monkeypatch):
    from app.core import vm_locks
    from app.routers.vms import hardware_opts

    conn = Conn()

    def open_conn(node=None):
        conn.lookupByName = lambda name: (
            conn.domain if name == "web" else (_ for _ in ()).throw(libvirt.libvirtError("no"))
        )
        conn.close = lambda: None
        return conn

    monkeypatch.setattr(hardware_opts, "open_conn", open_conn)
    admin = auth_headers("admin")
    r = client.get("/vms/web/hardware-options", headers=admin)
    assert r.status_code == 200 and r.json()["machines_plus_recentes"] == ["pc-q35-9.0"]
    r = client.put("/vms/web/disks/vda/options", json={"cache": "writeback", "iops": 100}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["disques"][0]["cache"] == "writeback"
    assert client.put("/vms/web/disks/vda/options", json={"cache": "turbo"}, headers=admin).status_code == 422
    assert client.put("/vms/web/boot-order", json={"ordre": ["sda"]}, headers=admin).json()["ordre_demarrage"] == [
        "sda"
    ]
    assert client.put("/vms/ghost/boot-order", json={"ordre": ["sda"]}, headers=admin).status_code == 404
    with vm_locks.claim("web", "a backup"):
        assert client.put("/vms/web/balloon", json={"actif": False}, headers=admin).status_code == 409
    viewer = auth_headers("viewer", role="observateur")
    assert client.get("/vms/web/hardware-options", headers=viewer).status_code == 200
    assert client.put("/vms/web/machine", json={"machine": "pc-q35-9.0"}, headers=viewer).status_code == 403
