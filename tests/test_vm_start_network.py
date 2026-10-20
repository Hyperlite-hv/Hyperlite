"""Starting a VM whose network is stopped says which network and what to do (libvirt only said "network 'default'
is not active"), before trying."""

import libvirt

from app.routers.vms import lifecycle


class _Net:
    def __init__(self, active):
        self.active = active

    def isActive(self):
        return 1 if self.active else 0


class _Domain:
    def __init__(self):
        self.started = False

    def isActive(self):
        return 0

    def XMLDesc(self, *_):
        return (
            "<domain><devices><interface type='network'><source network='default'/></interface>"
            "<interface type='network'><source network='lan'/></interface></devices></domain>"
        )

    def create(self):
        self.started = True


class _Conn:
    def __init__(self, domain, nets):
        self.domain, self.nets = domain, nets

    def lookupByName(self, name):
        return self.domain

    def networkLookupByName(self, name):
        if name not in self.nets:
            raise libvirt.libvirtError("no network")
        return self.nets[name]

    def getHostname(self):
        return "lab"

    def close(self):
        pass


def test_a_stopped_network_is_named_before_starting(client, auth_headers, monkeypatch):
    domain = _Domain()
    conn = _Conn(domain, {"default": _Net(False), "lan": _Net(True)})
    monkeypatch.setattr(lifecycle, "open_conn", lambda *a, **k: conn)
    r = client.post("/vms/web/start", headers=auth_headers("root"))
    assert r.status_code == 409 and "'default'" in r.json()["detail"] and "Network page" in r.json()["detail"]
    assert not domain.started
    assert lifecycle._stopped_networks(_Conn(domain, {"default": _Net(True)}), domain) == ["lan"]  # gone: stopped too


def test_a_vm_busy_with_a_restore_or_a_copy_is_not_started(client, auth_headers, monkeypatch):
    """A restore swaps the disks of a stopped VM after minutes of copying: started meanwhile, the VM kept writing to the
    replaced files, which the swap then deleted (#448, F10). As Proxmox's lock, the start is refused while an
    operation holds the VM."""
    from app.core import vm_locks

    domain = _Domain()
    conn = _Conn(domain, {"default": _Net(True), "lan": _Net(True)})
    monkeypatch.setattr(lifecycle, "open_conn", lambda *a, **k: conn)
    monkeypatch.setattr(lifecycle, "_domain_summary", lambda domain, *a, **k: {"name": "web"})
    headers = auth_headers("root")
    held = vm_locks.claim("web", "a backup restore")
    try:
        r = client.post("/vms/web/start", headers=headers)
        assert r.status_code == 409 and "a backup restore" in r.json()["detail"]
        assert not domain.started
    finally:
        held.release()
    assert client.post("/vms/web/start", headers=headers).status_code == 200  # once the restore is over
    assert domain.started


def test_start_at_boot_skips_a_vm_busy_with_an_operation(monkeypatch, database):
    from app.core import vm_boot, vm_locks

    domain = _Domain()
    conn = _Conn(domain, {})
    monkeypatch.setattr(vm_boot, "open_conn", lambda *a, **k: conn)
    monkeypatch.setattr(vm_boot, "sequence", lambda node: [{"name": "web", "position": 1, "delay_s": 0}])
    held = vm_locks.claim("web", "a backup restore")
    try:
        results = vm_boot.run_sequence()
    finally:
        held.release()
    assert results == [("web", "busy")] and not domain.started
