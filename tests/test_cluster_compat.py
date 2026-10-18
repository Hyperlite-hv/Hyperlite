"""Cluster compatibility diagnostics, exercised with fake libvirt connections."""

import libvirt
import pytest

from app.core import cluster_compat as cc

CAPS = (
    "<capabilities><host><cpu><arch>{arch}</arch></cpu></host>"
    "<guest><arch name='x86_64'>"
    "<machine canonical='pc-i440fx-7.2'>pc</machine><machine>q35</machine>{domain}"
    "</arch></guest>"
    "</capabilities>"
)


class FakeNetwork:
    def isActive(self):
        return True


class FakePool:
    def __init__(self, available_gib):
        self._available = available_gib * 1024**3

    def info(self):
        return [0, 0, 0, self._available]


class FakeConn:
    def __init__(
        self,
        arch="x86_64",
        kvm=True,
        networks=(),
        cpu_result=None,
        uefi=True,
        free_kib=8 * 1024 * 1024,
        qemu_version=7002022,
        disk_gib=100,
        existing_vms=(),
    ):
        self.arch, self.kvm, self.networks = arch, kvm, set(networks)
        self.cpu_result = libvirt.VIR_CPU_COMPARE_IDENTICAL if cpu_result is None else cpu_result
        self.uefi, self.free_kib, self.qemu_version = uefi, free_kib, qemu_version
        self.disk_gib, self.existing_vms = disk_gib, set(existing_vms)

    def getCapabilities(self):
        return CAPS.format(arch=self.arch, domain="<domain type='kvm'/>" if self.kvm else "<domain type='qemu'/>")

    def getVersion(self):
        return self.qemu_version

    def getLibVersion(self):
        return 9000000

    def compareCPU(self, xml, flags):
        return self.cpu_result

    def getCPUModelNames(self, arch, flags):
        return getattr(self, "cpu_models", None)  # None: the model list is not consulted

    def lookupByName(self, name):
        if name in self.existing_vms:
            return object()
        raise libvirt.libvirtError("domain not found")

    def networkLookupByName(self, name):
        if name in self.networks:
            return FakeNetwork()
        raise libvirt.libvirtError("network not found")

    def getDomainCapabilities(self, *args):
        return f"<domainCapabilities><os><loader supported='{'yes' if self.uefi else 'no'}'/></os></domainCapabilities>"

    def getMemoryStats(self, cell, flags):
        return {"free": self.free_kib, "cached": 0, "buffers": 0}

    def listAllStoragePools(self):
        return []

    def storagePoolLookupByName(self, name):
        return FakePool(self.disk_gib)


class FakeDomain:
    def __init__(self, xml, active=True, memory_kib=2 * 1024 * 1024):
        self._xml, self._active, self._memory = xml, active, memory_kib

    def XMLDesc(self, flags):
        return self._xml

    def name(self):
        return "vm1"

    def isActive(self):
        return self._active

    def info(self):
        return [1, 0, self._memory, 1, 0]

    def blockInfo(self, target, flags):
        return [20 * 1024**3, 0, 0]


SIMPLE_VM = """<domain type='kvm'><name>vm1</name><os><type arch='x86_64' machine='pc'>hvm</type></os>
<devices><disk type='file' device='disk'><driver type='qcow2'/><source file='/var/lib/libvirt/images/vm1.qcow2'/>
<target dev='vda'/></disk><interface type='network'><source network='default'/></interface></devices></domain>"""


def by_id(checks):
    return {check["id"]: check for check in checks}


def blocking(checks):
    return sorted(check["id"] for check in checks if check["status"] == "blocking")


def test_compatible_hosts_and_a_simple_vm_pass_every_check():
    checks = cc.check_vm_migration(FakeConn(), FakeConn(networks=["default"]), FakeDomain(SIMPLE_VM))
    assert blocking(checks) == []
    assert cc.report(checks)["resume"]["blocking"] is False


def test_different_architectures_block():
    assert "architecture" in blocking(cc.check_pair(FakeConn(), FakeConn(arch="aarch64")))


def test_missing_kvm_on_the_destination_blocks():
    assert "kvm" in blocking(cc.check_pair(FakeConn(), FakeConn(kvm=False)))


def test_older_destination_qemu_only_warns():
    checks = by_id(cc.check_pair(FakeConn(qemu_version=10000000), FakeConn(qemu_version=7002022)))
    assert checks["version_qemu"]["status"] == "warning"


def test_incompatible_cpu_blocks():
    checks = cc.check_pair(FakeConn(), FakeConn(cpu_result=libvirt.VIR_CPU_COMPARE_INCOMPATIBLE))
    assert "cpu" in blocking(checks)


def test_a_stopped_vm_cannot_be_live_migrated():
    checks = cc.check_vm_migration(FakeConn(), FakeConn(networks=["default"]), FakeDomain(SIMPLE_VM, active=False))
    assert "vm_state" in blocking(checks)


def test_name_collision_on_the_destination_blocks():
    dest = FakeConn(networks=["default"], existing_vms=["vm1"])
    assert "name_available" in blocking(cc.check_vm_migration(FakeConn(), dest, FakeDomain(SIMPLE_VM)))


def test_unknown_machine_type_blocks():
    xml = SIMPLE_VM.replace("machine='pc'", "machine='pc-i440fx-10.0'")
    checks = cc.check_vm_migration(FakeConn(), FakeConn(networks=["default"]), FakeDomain(xml))
    assert "machine" in blocking(checks)


def test_missing_network_on_the_destination_blocks():
    assert "network:default" in blocking(cc.check_vm_migration(FakeConn(), FakeConn(), FakeDomain(SIMPLE_VM)))


def test_block_device_disks_are_not_migratable():
    xml = SIMPLE_VM.replace(
        "<disk type='file' device='disk'><driver type='qcow2'/><source file='/var/lib/libvirt/images/vm1.qcow2'/>",
        "<disk type='block' device='disk'><driver type='raw'/><source dev='/dev/zvol/tank/vm1'/>",
    )
    checks = cc.check_vm_migration(FakeConn(), FakeConn(networks=["default"]), FakeDomain(xml))
    assert "disk:vda" in blocking(checks)


def test_insufficient_destination_disk_space_blocks():
    dest = FakeConn(networks=["default"], disk_gib=5)
    assert "disk_space" in blocking(cc.check_vm_migration(FakeConn(), dest, FakeDomain(SIMPLE_VM)))


def test_insufficient_destination_memory_blocks():
    dest = FakeConn(networks=["default"], free_kib=512 * 1024)
    assert "memory" in blocking(cc.check_vm_migration(FakeConn(), dest, FakeDomain(SIMPLE_VM)))


def test_passthrough_devices_block():
    xml = SIMPLE_VM.replace("</devices>", "<hostdev type='pci'/></devices>")
    checks = cc.check_vm_migration(FakeConn(), FakeConn(networks=["default"]), FakeDomain(xml))
    assert "devices" in blocking(checks)


def test_uefi_vm_needs_firmware_on_the_destination():
    xml = SIMPLE_VM.replace("<os>", "<os firmware='efi'>")
    dest = FakeConn(networks=["default"], uefi=False)
    assert "firmware" in blocking(cc.check_vm_migration(FakeConn(), dest, FakeDomain(xml)))


def test_a_crashing_check_becomes_a_warning_instead_of_failing_the_diagnostic():
    class Broken(FakeConn):
        def getMemoryStats(self, cell, flags):
            raise RuntimeError("boom")

    checks = by_id(cc.check_vm_migration(FakeConn(), Broken(networks=["default"]), FakeDomain(SIMPLE_VM)))
    assert checks["memory"]["status"] == "warning"


@pytest.mark.parametrize("version,expected", [(9000000, "9.0.0"), (11003001, "11.3.1")])
def test_version_formatting(version, expected):
    assert cc._ver(version) == expected


def test_the_vm_cpu_is_compared_as_defined_not_as_it_runs(monkeypatch):
    """A running VM's live CPU lists every feature its host gave it (check='full'), which libvirt finds incompatible
    even with the host the VM runs on: every live migration of a VM with a cluster CPU model was refused. The
    definition the destination receives (VIR_DOMAIN_XML_MIGRATABLE) is compared."""
    live = SIMPLE_VM.replace(
        "<devices>", "<cpu mode='custom' match='exact' check='full'><model>Skylake-Client-IBRS</model></cpu><devices>"
    )
    defined = SIMPLE_VM.replace(
        "<devices>",
        "<cpu mode='custom' match='exact' check='partial'><model>Skylake-Client-IBRS</model></cpu><devices>",
    )

    class Domain(FakeDomain):
        def XMLDesc(self, flags):
            return defined if flags & libvirt.VIR_DOMAIN_XML_MIGRATABLE else live

    class Dst(FakeConn):
        def compareCPU(self, xml, flags):
            return libvirt.VIR_CPU_COMPARE_INCOMPATIBLE if 'check="full"' in xml else libvirt.VIR_CPU_COMPARE_SUPERSET

    checks = by_id(cc.check_vm_migration(FakeConn(), Dst(), Domain(live)))
    assert checks["cpu_vm"]["status"] == cc.OK


def test_a_disk_on_shared_storage_based_on_a_local_image_is_refused(monkeypatch):
    """The disk was on NFS, its base image in the source node's own directory: libvirt failed mid-migration."""
    disk = "<disk type='file' device='disk'><driver type='qcow2'/><source file='/var/lib/libvirt/hyperlite-pools/nfs/vm1.qcow2'/>"
    vm = SIMPLE_VM.replace(
        "<disk type='file' device='disk'><driver type='qcow2'/><source file='/var/lib/libvirt/images/vm1.qcow2'/>", disk
    )
    chain = vm.replace(
        "<target dev='vda'/>",
        "<target dev='vda'/><backingStore type='file'><format type='qcow2'/>"
        "<source file='/var/lib/libvirt/images/base/debian.qcow2'/><backingStore/></backingStore>",
    )
    monkeypatch.setattr(cc, "_netfs_pools", lambda conn: {"nfs": "/var/lib/libvirt/hyperlite-pools/nfs"})

    class Domain(FakeDomain):
        def XMLDesc(self, flags):
            return vm if flags & libvirt.VIR_DOMAIN_XML_MIGRATABLE else chain

    checks = by_id(cc.check_vm_migration(FakeConn(), FakeConn(), Domain(chain)))
    assert checks["disk:vda"]["status"] == cc.BLOCKING and "only this node has" in checks["disk:vda"]["message"]


def test_a_cpu_model_unknown_to_the_destination_is_found_in_any_language():
    """libvirtd writes its errors in the server's language ("Modèle de CPU inconnu" on a French one): the destination's
    model list is asked instead, and the CPU driver's error is recognised by its code."""
    xml = "<cpu><arch>x86_64</arch><model>Sapphire-Rapids</model></cpu>"
    dst = FakeConn()
    dst.cpu_models = ["qemu64", "Skylake-Client"]
    r = cc._cpu_result("cpu", dst, xml, "Physical CPU of the source")
    assert r["status"] == cc.WARNING and "Sapphire-Rapids" in r["message"]

    class FrenchDst(FakeConn):
        def compareCPU(self, xml, flags):
            e = libvirt.libvirtError("erreur interne : Modèle de CPU inconnu Sapphire-Rapids")
            e.err = (libvirt.VIR_ERR_INTERNAL_ERROR, libvirt.VIR_FROM_CPU, e.args[0], 2, "", None, None, 0, 0)
            raise e

    r = cc._cpu_result("cpu", FrenchDst(), xml, "Physical CPU of the source")
    assert r["status"] == cc.WARNING and "Sapphire-Rapids" in r["message"]

    dst.cpu_models = ["qemu64", "Sapphire-Rapids"]  # known: compared as before
    assert cc._cpu_result("cpu", dst, xml, "Physical CPU of the source")["status"] == cc.OK
