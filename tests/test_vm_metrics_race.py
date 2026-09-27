"""Live metrics must not fail when the VM stops while it is being sampled."""

import libvirt

from app.routers.vms import runtime


class _Domain:
    """Active at the first check, then stops as soon as it is sampled."""

    def __init__(self):
        self.active = True

    def isActive(self):
        return 1 if self.active else 0

    def XMLDesc(self, _flags):
        return "<domain><devices></devices></domain>"

    def getCPUStats(self, _total):
        self.active = False
        raise libvirt.libvirtError("Requested operation is not valid: domain is not running")


class _Conn:
    def __init__(self, domain):
        self._domain = domain

    def lookupByName(self, _name):
        return self._domain

    def close(self):
        pass


def test_a_vm_stopping_during_sampling_is_reported_stopped_not_a_500(monkeypatch):
    monkeypatch.setattr(runtime, "open_conn", lambda: _Conn(_Domain()))
    monkeypatch.setattr(runtime, "log_action", lambda *a, **k: None)
    result = runtime.get_vm_metrics("vm1", user={"username": "tester"})
    assert result["etat"] == "arrete"
    assert result["cpu_pourcent"] is None
