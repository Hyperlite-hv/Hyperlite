"""The VMs' root work in hyperlite-daemon, part 1: power, libvirt's autostart flag, the pre-stop hook script and the
guest agents' addresses (app/daemon/ops/vm.py, docs/design/privilege-separation.md, section 13). An operation names
the VM; the daemon looks it up in this node's libvirt itself and checks its state again."""

import libvirt
import pytest

from app.core import privileged
from app.daemon.registry import DaemonError


class _Dom:
    def __init__(self, name, running=False, agent=False):
        self._name, self.running, self.agent, self.calls, self.flag = name, running, agent, [], None

    def name(self):
        return self._name

    def UUIDString(self):
        return f"uuid-{self._name}"

    def isActive(self):
        return self.running

    def XMLDesc(self, flags=0):
        state = "connected" if self.agent else "disconnected"
        return (
            f"<domain><name>{self._name}</name><devices><channel type='unix'>"
            f"<target type='virtio' name='org.qemu.guest_agent.0' state='{state}'/></channel></devices></domain>"
        )

    def create(self):
        self.calls.append("create")
        self.running = True

    def destroy(self):
        self.calls.append("destroy")
        self.running = False

    def shutdown(self):
        self.calls.append("acpi-shutdown")

    def shutdownFlags(self, flags):
        self.calls.append("agent-shutdown")

    def reboot(self, flags=0):
        self.calls.append("agent-reboot" if flags else "acpi-reboot")

    def agentSetResponseTimeout(self, timeout, flags):
        pass

    def setAutostart(self, flag):
        self.flag = flag


class _Conn:
    def __init__(self, *domains):
        self.domains = {d.name(): d for d in domains}

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]


@pytest.fixture()
def host(daemon_libvirt):
    return daemon_libvirt(_Conn(_Dom("web"), _Dom("db", running=True, agent=True), _Dom("old", running=True)))


def test_power_names_its_action_and_its_vm(host):
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.power", vm="web", action="format")
    assert e.value.kind == "invalid_arguments"
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.power", vm="ghost", action="start")
    assert e.value.kind == "not_found"


def test_the_state_is_checked_again_by_the_daemon(host):
    for vm, action in [("db", "start"), ("web", "stop"), ("web", "shutdown"), ("web", "reset")]:
        with pytest.raises(DaemonError) as e:
            privileged.run("vm.power", vm=vm, action=action)
        assert e.value.kind == "in_use", (vm, action)
    assert all(d.calls == [] for d in host.domains.values())


def test_a_shutdown_asks_the_agent_when_it_is_there_else_acpi(host):
    assert privileged.run("vm.power", vm="db", action="shutdown") == {"method": "agent"}
    assert privileged.run("vm.power", vm="old", action="reboot") == {"method": "acpi"}
    assert host.domains["db"].calls == ["agent-shutdown"] and host.domains["old"].calls == ["acpi-reboot"]
    privileged.run("vm.power", vm="old", action="reset")
    assert host.domains["old"].calls[-2:] == ["destroy", "create"]
    privileged.run("vm.power", vm="web", action="start")
    assert host.domains["web"].running


def test_libvirts_autostart_flag(host):
    privileged.run("vm.autostart", vm="web", autostart=False)
    assert host.domains["web"].flag == 0


def test_a_vm_without_a_script_runs_no_pre_stop(host):
    assert privileged.run("vm.pre_stop", vm="web") is None


def test_the_agents_are_asked_through_the_daemons_libvirt(host, fake_agent):
    from app.core import guest_agent

    guest_agent.forget()
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.agent_ipv4", vms=["x" * 65])
    assert e.value.kind == "invalid_arguments"
    assert privileged.run("vm.agent_ipv4", vms=["ghost"]) == {}


# --- Part 2a: a VM's processors, memory, limits, pinning and advanced hardware ---


def test_a_running_vms_resources_are_kept_whatever_the_api_checked(host):
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.set_resources", vm="db", vcpu=4)
    assert e.value.kind == "in_use"


@pytest.mark.parametrize(
    "op, args",
    [
        ("vm.set_disk_options", {"vm": "web", "target": "../vda"}),
        ("vm.set_resources", {"vm": "web", "vcpu": 0}),
        ("vm.set_limits", {"vm": "web", "cpu_shares": 1}),
        ("vm.set_balloon", {"vm": "web", "active": True, "minimum_mb": 1}),
        ("vm.set_machine", {"vm": "web", "machine": ""}),
        ("vm.set_boot_order", {"vm": "web", "position": []}),
    ],
)
def test_the_fields_are_bounded_by_the_daemon(host, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "invalid_arguments", op


def test_a_definition_is_changed_on_a_vm_of_this_node_only(host):
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.set_machine", vm="ghost", machine="pc-q35-9.0")
    assert e.value.kind == "not_found"


# --- Part 2b: a VM's devices ---


@pytest.mark.parametrize(
    "op, args",
    [
        ("vm.attach_disk", {"vm": "web", "pool": "default", "volume": "d.qcow2", "target": "../vda"}),
        ("vm.attach_disk", {"vm": "web", "pool": "default", "path": "/etc/shadow", "target": "vdb"}),
        ("vm.detach_interface", {"vm": "web", "mac": "eth0"}),
        ("vm.set_cdrom", {"vm": "web", "iso": "x.iso", "target": "sda"}),
        ("vm.set_interface", {"vm": "web", "network": "lan", "xml": "<interface/>"}),
    ],
)
def test_a_device_is_named_never_given_as_a_path_or_a_definition(host, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "invalid_arguments", op


def test_an_iso_is_found_in_the_daemons_own_library(host, tmp_path, monkeypatch):
    from app.core import disk_library

    monkeypatch.setattr(disk_library, "ISOS", tmp_path)
    for iso in ("../../etc/shadow.iso", "missing.iso"):
        with pytest.raises(DaemonError) as e:
            privileged.run("vm.set_cdrom", vm="web", iso=iso)
        assert e.value.kind == "not_found", iso
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.set_cdrom", vm="web", iso="notes.txt")
    assert e.value.kind == "refused"


def test_the_last_interface_is_kept_whatever_the_api_checked(host):
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.detach_interface", vm="web", mac="52:54:00:00:00:01")
    assert e.value.kind == "refused"


# --- Part 2c: a VM's firewall, hook script, name and cloud-init drive ---


@pytest.mark.parametrize(
    "op, args",
    [
        (
            "vm.set_firewall",
            {
                "vm": "web",
                "config": {
                    "default_policy": "drop",
                    "rules": [{"action": "accept", "direction": "in", "protocol": "tcp' />", "port": 22}],
                },
            },
        ),
        ("vm.set_firewall", {"vm": "web", "config": {"default_policy": "reject", "rules": []}}),
        ("vm.set_hookscript", {"vm": "web", "script": "x" * 101}),
        ("vm.rename", {"vm": "web", "new_name": "y" * 65}),
        ("vm.cloudinit_write", {"vm": "web", "account": "ana", "custom": {}, "path": "/etc/shadow"}),
    ],
)
def test_the_fields_are_checked_by_the_daemon(host, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "invalid_arguments", op


def test_a_running_vm_keeps_its_name_whatever_the_api_checked(host):
    host.domains["db"].snapshotNum = lambda flags=0: 0
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.rename", vm="db", new_name="db2")
    assert e.value.kind == "in_use"
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.rename", vm="web", new_name="bad name!")
    assert e.value.kind == "refused"


def test_only_a_script_of_the_root_library_is_attached(host):
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.set_hookscript", vm="web", script="../../tmp/evil.sh")
    assert e.value.kind == "refused"


SPEC = {"name": "new", "vcpu": 1, "memory_mb": 512, "disks": [{"size_gb": 1}], "iso": "debian.iso"}


@pytest.mark.parametrize(
    "args",
    [
        {"spec": {**SPEC, "xml": "<domain/>"}},
        {"spec": {**SPEC, "disks": [{"size_gb": 1, "path": "/etc/shadow"}]}},
        {"spec": SPEC, "pending": ["a.iso", "b.iso", "c.iso", "d.iso", "e.iso"]},
    ],
)
def test_a_creation_takes_a_specification_never_a_definition_or_a_path(host, args):
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.create", **args)
    assert e.value.kind == "invalid_arguments"


def test_a_creation_is_checked_again_by_the_daemon(daemon_libvirt, monkeypatch, tmp_path):
    """What the API checked does not count: the daemon reads its own library and the VMs of this node, and answers every
    refusal at once, before anything is made."""
    from unittest.mock import Mock

    from app.core import disk_library, vm_create

    monkeypatch.setattr(disk_library, "ISOS", tmp_path)
    monkeypatch.setattr(vm_create, "validate_vm_resources", lambda *a: [])
    monkeypatch.setattr(vm_create.firmware, "check_choice", lambda conn, choice: None)
    monkeypatch.setattr(vm_create, "create_disk", Mock(side_effect=AssertionError("nothing is made")))
    conn = daemon_libvirt(Mock())
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.create", spec={**SPEC, "name": "web"}, check_only=False)
    assert e.value.kind == "refused"
    assert any("already exists" in m for m in e.value.detail) and any("not found" in m for m in e.value.detail)
    conn.defineXML.assert_not_called()
    (tmp_path / "debian.iso").touch()
    conn.lookupByName.side_effect = libvirt.libvirtError("no domain")
    assert privileged.run("vm.create", spec=SPEC, pending=["debian.iso"], check_only=True) is None
    conn.defineXML.assert_not_called()


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("vm.clone", {"vm": "web", "new_name": "copy", "disk": "/etc/shadow"}),
        ("vm.clone", {"vm": "web", "new_name": "c" * 65}),
        ("template.make", {"vm": "web", "template": "base", "created_by": "ana", "xml": "<domain/>"}),
        ("template.deploy", {"template": "base", "new_name": "copy", "disk_path": "/etc/shadow"}),
        ("template.rename", {"template": "base", "new_name": "n" * 65}),
        ("template.delete", {"template": "base", "path": "/var/lib/hyperlite/templates/base.qcow2"}),
    ],
)
def test_a_clone_or_a_template_is_named_never_given_as_a_path_or_a_definition(host, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "invalid_arguments"


def test_a_clone_is_checked_again_by_the_daemon(host):
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.clone", vm="db", new_name="copy", check_only=True)
    assert e.value.kind == "in_use"  # running
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.clone", vm="gone", new_name="copy", check_only=True)
    assert e.value.kind == "not_found"


class _ZfsDom(_Dom):
    def XMLDesc(self, flags=0):
        return (
            f"<domain><name>{self._name}</name><devices><disk type='block' device='disk'>"
            "<source dev='/dev/zvol/tank/vm-zfs'/><target dev='vda'/></disk></devices></domain>"
        )


def test_a_snapshot_is_checked_again_by_the_daemon(daemon_libvirt):
    daemon_libvirt(_Conn(_ZfsDom("zfs", running=True)))
    for name in ("a,b", "x%y", "pre@x"):  # ZFS reads them as lists and ranges of snapshots
        with pytest.raises(DaemonError) as e:
            privileged.run("vm.snapshot_delete", vm="zfs", name=name)
        assert e.value.kind == "refused"
    with pytest.raises(DaemonError) as e:  # a rollback under a running QEMU corrupts the guest's disk
        privileged.run("vm.snapshot_restore", vm="zfs", name="before", check_only=True)
    assert e.value.kind == "in_use"
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.snapshot_create", vm="zfs", name="s1", xml="<domainsnapshot/>")
    assert e.value.kind == "invalid_arguments"
