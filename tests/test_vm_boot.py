"""Start at boot: the per-node sequence, once per boot of that node, and the API that edits it."""

import libvirt
import pytest

from app.core import vm_boot


class _Domain:
    def __init__(self, name, active=False, fails=False, autostart=False):
        self.name, self.active, self.fails, self.flag = name, active, fails, autostart

    def isActive(self):
        return self.active

    def create(self):
        if self.fails:
            raise libvirt.libvirtError("cannot start")
        self.active = True

    def autostart(self):
        return 1 if self.flag else 0

    def setAutostart(self, value):
        self.flag = bool(value)


class _Conn:
    def __init__(self, domains):
        self.domains = {d.name: d for d in domains}
        self.started = []

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def close(self):
        pass


@pytest.fixture()
def host(database, monkeypatch, daemon_libvirt):
    conn = _Conn([_Domain("db"), _Domain("app"), _Domain("web", active=True), _Domain("broken", fails=True)])
    opened = []
    monkeypatch.setattr(vm_boot, "open_conn", lambda node=None: opened.append(node) or conn)
    daemon_libvirt(conn)  # hyperlite-daemon starts this node's VMs, in this process in the tests
    conn.opened = opened
    return conn


def test_the_sequence_follows_the_order_then_the_name(database):
    vm_boot.set_setting("zeta", True, None, 0)
    vm_boot.set_setting("app", True, 20, 0)
    vm_boot.set_setting("db", True, 10, 30)
    vm_boot.set_setting("alpha", True, None, 0)
    vm_boot.set_setting("off", False, 1, 0)
    vm_boot.set_setting("elsewhere", True, 1, 0, node="n2")
    assert [v["name"] for v in vm_boot.sequence()] == ["db", "app", "alpha", "zeta"]
    assert [v["name"] for v in vm_boot.sequence("n2")] == ["elsewhere"]


def test_the_sequence_starts_what_is_stopped_waits_and_goes_on_after_a_failure(host):
    vm_boot.set_setting("db", True, 1, 30)
    vm_boot.set_setting("broken", True, 2, 5)
    vm_boot.set_setting("web", True, 3, 0)
    vm_boot.set_setting("app", True, 4, 0)
    vm_boot.set_setting("gone", True, 5, 0)
    waits = []
    results = vm_boot.run_sequence(sleep=waits.append)
    assert results == [
        ("db", "started"),
        ("broken", "failed"),
        ("web", "already_enabled"),
        ("app", "started"),
        ("gone", "missing"),
    ]
    assert waits == [30]  # a failed VM does not make the next one wait
    assert host.domains["db"].active and host.domains["app"].active


def test_it_runs_once_per_boot_of_a_node_not_at_each_service_restart(host):
    vm_boot.set_setting("db", True, 1, 0)
    assert vm_boot.boot_if_new("local", "boot-1") == [("db", "started")]
    host.domains["db"].active = False  # stopped on purpose by an administrator
    assert vm_boot.boot_if_new("local", "boot-1") == []  # the service restarted, the host did not
    assert not host.domains["db"].active
    assert vm_boot.boot_if_new("local", "boot-2") == [("db", "started")]
    assert vm_boot.boot_if_new("local", None) == []  # boot id unreadable: nothing is started
    # Each node has its own boots.
    assert vm_boot.claim_boot("n2", "boot-1") is True


def test_a_remote_node_sequence_opens_that_node(host):
    vm_boot.set_setting("app", True, 1, 0, node="n2")
    assert vm_boot.boot_if_new("n2", "b") == [("app", "started")]
    assert host.opened == ["n2"]


def test_the_setting_follows_a_migration(database):
    vm_boot.set_setting("db", True, 5, 10)
    assert vm_boot.follow_migration("db", None, "n2") is True
    assert vm_boot.get_setting("db") == {"autostart": False, "position": None, "delay_s": 0}
    assert vm_boot.get_setting("db", "n2") == {"autostart": True, "position": 5, "delay_s": 10}
    assert vm_boot.follow_migration("db", "n2", "local") is True
    assert vm_boot.get_setting("db")["position"] == 5
    assert vm_boot.follow_migration("nothing", None, "n2") is False


def test_the_api_reads_and_writes_the_setting_and_takes_over_libvirt_autostart(
    client, auth_headers, monkeypatch, daemon_libvirt
):
    from app.routers.vms import settings

    conn = _Conn([_Domain("db", autostart=True)])
    monkeypatch.setattr(settings, "open_conn", lambda node=None: conn)
    daemon_libvirt(conn)  # hyperlite-daemon turns libvirt's flag off, in this process in the tests
    admin = auth_headers("admin")
    r = client.get("/vms/db/boot", headers=admin)
    assert r.status_code == 200
    assert r.json() == {"autostart": False, "position": None, "delay_s": 0, "autostart_libvirt": True}

    r = client.put("/vms/db/boot", json={"autostart": True, "position": 10, "delay_s": 30}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json() == {"autostart": True, "position": 10, "delay_s": 30, "autostart_libvirt": False}
    assert conn.domains["db"].flag is False

    assert client.put("/vms/db/boot", json={"autostart": True, "delay_s": 99999}, headers=admin).status_code == 422
    assert client.put("/vms/db/boot", json={"autostart": True, "position": -1}, headers=admin).status_code == 422
    assert client.get("/vms/ghost/boot", headers=admin).status_code == 404
    viewer = auth_headers("viewer", role="viewer")
    assert client.get("/vms/db/boot", headers=viewer).status_code == 200
    assert client.put("/vms/db/boot", json={"autostart": False}, headers=viewer).status_code == 403


def test_a_manager_may_set_it_an_operator_may_not(database):
    from app.core import permissions

    permissions.create_acl("user", "mia", "manager", "vm", "db")
    permissions.create_acl("user", "otto", "operator", "vm", "db")
    assert permissions.has_privilege({"username": "mia", "role": "user"}, "db", "vm.options")
    assert not permissions.has_privilege({"username": "otto", "role": "user"}, "db", "vm.options")
