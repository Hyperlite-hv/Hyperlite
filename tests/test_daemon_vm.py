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
