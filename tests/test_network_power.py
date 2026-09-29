"""A network can be started, stopped (running VMs named, confirmation required) and set to start at boot."""

import pytest


class _Net:
    def __init__(self, name, active=False, autostart=False, fail=None):
        self._name, self.active, self.auto, self.fail = name, active, autostart, fail

    def name(self):
        return self._name

    def UUIDString(self):
        return "u"

    def isActive(self):
        return 1 if self.active else 0

    def autostart(self):
        return 1 if self.auto else 0

    def XMLDesc(self, flags=0):
        return f"<network><name>{self._name}</name><forward mode='nat'/><bridge name='virbr0'/></network>"

    def create(self):
        import libvirt

        if self.fail:
            raise libvirt.libvirtError(self.fail)
        self.active = True

    def destroy(self):
        self.active = False

    def setAutostart(self, value):
        self.auto = bool(value)


class _Dom:
    def __init__(self, name, network):
        self._name, self.network = name, network

    def name(self):
        return self._name

    def XMLDesc(self, flags=0):
        return f"<domain><devices><interface type='network'><source network='{self.network}'/></interface></devices></domain>"


class _Conn:
    def __init__(self):
        self.nets = {
            "default": _Net("default"),
            "lan": _Net("lan", active=True),
            "broken": _Net("broken", fail="address in use"),
        }
        self.running = [_Dom("web", "lan"), _Dom("db", "other")]

    def networkLookupByName(self, name):
        import libvirt

        if name not in self.nets:
            raise libvirt.libvirtError("no network")
        return self.nets[name]

    def listAllDomains(self, flags=0):
        return self.running

    def close(self):
        pass


@pytest.fixture()
def conn(database, monkeypatch):
    from app.routers import network

    c = _Conn()
    monkeypatch.setattr(network, "open_conn", lambda node=None: c)
    return c


def test_a_stopped_network_can_be_started_and_set_to_start_at_boot(conn, client, auth_headers):
    admin = auth_headers("admin")
    r = client.post("/networks/default/start", headers=admin)
    assert r.status_code == 200 and r.json()["actif"] is True
    r = client.put("/networks/default/autostart", json={"autostart": True}, headers=admin)
    assert r.status_code == 200 and r.json()["autostart"] is True
    assert client.post("/networks/default/start", headers=admin).status_code == 200  # already running: no error


def test_a_start_failure_says_why(conn, client, auth_headers):
    r = client.post("/networks/broken/start", headers=auth_headers("admin"))
    assert r.status_code == 500 and "address in use" in r.text


def test_stopping_names_the_running_vms_and_needs_a_confirmation(conn, client, auth_headers):
    admin = auth_headers("admin")
    r = client.post("/networks/lan/stop", headers=admin)
    assert r.status_code == 400 and "web" in r.text and "db" not in r.text
    assert conn.nets["lan"].active
    r = client.post("/networks/lan/stop?confirm=true", headers=admin)
    assert r.status_code == 200 and r.json()["actif"] is False


def test_only_administrators_change_a_network_and_unknown_ones_are_404(conn, client, auth_headers):
    viewer = auth_headers("viewer", role="observateur")
    assert client.post("/networks/default/start", headers=viewer).status_code == 403
    assert client.put("/networks/default/autostart", json={"autostart": True}, headers=viewer).status_code == 403
    assert client.post("/networks/ghost/start", headers=auth_headers("admin")).status_code == 404
