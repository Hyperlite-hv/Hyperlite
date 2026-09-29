"""A container's own page: its details, its resources, DNS servers and start at boot, and its task history."""

import libvirt
import pytest

from app.core import container_config, tasks

XML = """<domain type='lxc'><name>web</name><memory unit='KiB'>524288</memory>
<currentMemory unit='KiB'>524288</currentMemory><vcpu>1</vcpu>
<devices><interface type='network'><mac address='52:54:00:aa:bb:cc'/><source network='default'/></interface></devices>
</domain>"""


class _Domain:
    def __init__(self, conn, active=True):
        self.conn, self.active, self.flag, self.live_mem = conn, active, 0, None

    def name(self):
        return "web"

    def XMLDesc(self, flags=0):
        return self.conn.xml

    def isActive(self):
        return self.active

    def info(self):
        return [1 if self.active else 5, 524288, 524288, 1, 0]

    def ID(self):
        return 7

    def UUIDString(self):
        return "u-web"

    def autostart(self):
        return self.flag

    def setAutostart(self, value):
        self.flag = value

    def setMemoryFlags(self, kib, flags):
        if kib > 1024 * 1024:
            raise libvirt.libvirtError("above the live maximum")
        self.live_mem = kib

    def interfaceAddresses(self, *_a):
        return {}


class _Conn:
    def __init__(self):
        self.xml = XML
        self.domain = _Domain(self)

    def lookupByName(self, name):
        if name != "web":
            raise libvirt.libvirtError("no domain")
        return self.domain

    def defineXML(self, xml):
        self.xml = xml

    def close(self):
        pass


@pytest.fixture()
def lxc(monkeypatch, tmp_path):
    from app.routers import containers

    conn = _Conn()
    rootfs = tmp_path / "web"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "etc" / "resolv.conf").write_text("nameserver 1.1.1.1\nnameserver 9.9.9.9\n")
    monkeypatch.setattr(containers, "open_lxc_conn", lambda: conn)
    monkeypatch.setattr(container_config, "container_rootfs_path", lambda name: tmp_path / name)
    conn.rootfs = rootfs
    return conn


def test_the_details_show_interfaces_dns_and_start_at_boot(client, auth_headers, lxc):
    r = client.get("/containers/web", headers=auth_headers("admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["interfaces"] == [{"reseau": "default", "mac": "52:54:00:aa:bb:cc"}]
    assert body["dns"] == ["1.1.1.1", "9.9.9.9"]
    assert body["demarrage_auto"] is False


def test_resources_dns_and_start_at_boot_are_changed(client, auth_headers, lxc):
    admin = auth_headers("admin")
    r = client.patch(
        "/containers/web", json={"memory_mb": 768, "dns": ["10.0.0.53", " 2606:4700::1111 "]}, headers=admin
    )
    assert r.status_code == 200, r.text
    assert '<memory unit="KiB">786432</memory>' in lxc.xml.replace("'", '"')
    assert lxc.domain.live_mem == 786432  # memory applies live
    assert r.json()["a_redemarrer"] is False
    assert (lxc.rootfs / "etc" / "resolv.conf").read_text() == "nameserver 10.0.0.53\nnameserver 2606:4700::1111\n"

    r = client.patch("/containers/web", json={"vcpu": 2, "demarrage_auto": True}, headers=admin)
    assert r.json()["a_redemarrer"] is True  # the CPU count is read when the container starts
    assert lxc.domain.flag == 1
    assert client.patch("/containers/web", json={"memory_mb": 4096}, headers=admin).json()["a_redemarrer"] is True


def test_bad_changes_are_refused(client, auth_headers, lxc):
    admin = auth_headers("admin")
    assert client.patch("/containers/web", json={"dns": ["not-an-ip"]}, headers=admin).status_code == 422
    assert client.patch("/containers/web", json={"dns": ["1.1.1.1"] * 4}, headers=admin).status_code == 422
    assert client.patch("/containers/web", json={"dns": []}, headers=admin).status_code == 422
    assert client.patch("/containers/web", json={}, headers=admin).status_code == 422
    assert client.patch("/containers/ghost", json={"vcpu": 1}, headers=admin).status_code == 404
    viewer = auth_headers("viewer", role="observateur")
    assert client.patch("/containers/web", json={"vcpu": 2}, headers=viewer).status_code == 403


def test_a_resolver_link_is_replaced_by_a_file_never_followed(client, auth_headers, lxc, tmp_path):
    outside = tmp_path / "outside.conf"
    outside.write_text("keep me")
    (lxc.rootfs / "etc" / "resolv.conf").unlink()
    (lxc.rootfs / "etc" / "resolv.conf").symlink_to(outside)
    admin = auth_headers("admin")
    assert client.get("/containers/web", headers=admin).json()["dns"] is None
    r = client.patch("/containers/web", json={"dns": ["10.0.0.1"]}, headers=admin)
    assert r.status_code == 200
    assert outside.read_text() == "keep me"
    assert not (lxc.rootfs / "etc" / "resolv.conf").is_symlink()


def test_an_objects_history_matches_its_exact_name_and_kind(client, auth_headers, database):
    for type_, cible in (("start_vm", "web"), ("start_vm", "web-02"), ("backup_container", "web"), ("clone_vm", "db")):
        tasks.finish_task(tasks.create_task(type_, cible), "termine")
    admin = auth_headers("admin")
    vm = client.get("/tasks?objet=web&famille=vm", headers=admin).json()
    assert [(t["type"], t["cible"]) for t in vm] == [("start_vm", "web")]
    ct = client.get("/tasks?objet=web&famille=container", headers=admin).json()
    assert [(t["type"], t["cible"]) for t in ct] == [("backup_container", "web")]
    assert client.get("/tasks?famille=other", headers=admin).status_code == 422
