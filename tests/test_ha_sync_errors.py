"""What the HA sync and the manual recovery do when libvirt or SSH fails (found by ECC's python-reviewer): an error
proves nothing about the VM, and a failed attempt leaves nothing behind that refuses the next one."""

import libvirt
import pytest

from app.core import cluster_lead, ha


class _Conn:
    def __init__(self, error):
        self.error = error

    def lookupByName(self, name):
        raise self.error

    def close(self):
        pass


def _error(code):
    e = libvirt.libvirtError("libvirt says no")
    e.err = (code, 0, "libvirt says no", 0, "", "", "", 0, 0)
    return e


@pytest.mark.parametrize(
    ("code", "disabled"), [(libvirt.VIR_ERR_NO_DOMAIN, ["web"]), (libvirt.VIR_ERR_SYSTEM_ERROR, [])]
)
def test_only_a_vm_libvirt_does_not_know_loses_its_protection(database, monkeypatch, code, disabled):
    off = []
    monkeypatch.setattr(ha, "list_protected", lambda: [{"vm_name": "web", "node": "local"}])
    monkeypatch.setattr(ha, "open_conn", lambda key=None, **_k: _Conn(_error(code)))
    monkeypatch.setattr(ha, "disable_protection", lambda vm, by: off.append(vm))
    monkeypatch.setattr(cluster_lead, "in_cluster", lambda: True)
    ha.sync_protected_vms()
    assert off == disabled  # a connection lost or a libvirtd restart is no "VM not found"


def test_a_failed_manual_recovery_undefines_the_copy_it_left(database, monkeypatch):
    row = {"vm_name": "web", "node": "pve-a", "domain_xml": "<domain><name>web</name></domain>"}
    monkeypatch.setattr(ha, "get_protected", lambda vm: row)
    monkeypatch.setattr(ha, "_attempt_ssh_fence", lambda node, vm: (True, "killed"))
    forgotten, starts = [], []

    def start_on(row, target, reuse_stopped=False):
        starts.append(reuse_stopped)
        raise libvirt.libvirtError("disk locked")

    monkeypatch.setattr(ha, "start_on", start_on)
    monkeypatch.setattr(ha, "forget_on", lambda row, target: forgotten.append(target))
    with pytest.raises(RuntimeError, match="Recovery failed"):
        ha.recover("web", "pve-b", "admin")
    assert forgotten == ["pve-b"] and starts == [True]
