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
