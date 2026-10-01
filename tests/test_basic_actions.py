"""Renaming VMs, containers and nodes, the root shell of a container, stopping an application container, and the
environment variables some images need. libvirt and /proc are replaced by fakes."""

import json
import xml.etree.ElementTree as ET

import libvirt
import pytest


def _missing():
    e = libvirt.libvirtError("not found")
    e.get_error_code = lambda: libvirt.VIR_ERR_NO_DOMAIN
    return e


# ---- Program lookup inside an image ----


def test_an_absolute_link_is_resolved_inside_the_image(tmp_path):
    from app.core.container_builder import resolve_init

    rootfs = tmp_path / "rootfs"
    (rootfs / "bin").mkdir(parents=True)
    (rootfs / "bin" / "busybox").write_text("")
    (rootfs / "bin" / "sh").symlink_to("/bin/busybox")  # alpine: an absolute link, meant for inside the container
    assert resolve_init(rootfs, {"args": ["sh"], "env": {"PATH": "/usr/bin:/bin"}}) == "/bin/sh"


def test_a_link_to_a_host_directory_is_not_followed(tmp_path):
    from app.core.container_builder import exists_in_root, resolve_init

    host = tmp_path / "host"
    host.mkdir()
    (host / "tool").write_text("")  # exists on the host only
    rootfs = tmp_path / "rootfs"
    (rootfs / "usr").mkdir(parents=True)
    (rootfs / "usr" / "local").symlink_to(host)  # absolute: rootfs/<host path>, which does not exist
    (rootfs / "loop").symlink_to("/loop")
    with pytest.raises(ValueError, match="not found"):
        resolve_init(rootfs, {"args": ["tool"], "env": {"PATH": "/usr/local"}})
    assert not exists_in_root(rootfs, "/../../../" + str(host / "tool"))
    assert not exists_in_root(rootfs, "/loop/x")


# ---- Environment variables an image needs ----


@pytest.mark.parametrize(
    ("image", "repository"),
    [
        ("postgres:16", "postgres"),
        ("docker.io/library/postgres:16-alpine@sha256:ab", "postgres"),
        ("library/mysql", "mysql"),
        ("mirror.gcr.io/library/mariadb:11", "mariadb"),
        ("mcr.microsoft.com/mssql/server:2022-latest", "mcr.microsoft.com/mssql/server"),
        ("localhost:5000/postgres", "localhost:5000/postgres"),
        ("ghcr.io/foo/bar:1", "ghcr.io/foo/bar"),
    ],
)
def test_image_names_are_reduced_to_their_repository(image, repository):
    from app.core.image_env import repository as reduce

    assert reduce(image) == repository


def test_postgres_needs_a_password_or_an_alternative():
    from app.core.image_env import missing

    assert missing("postgres:16", {}) and "POSTGRES_PASSWORD" in missing("postgres:16", {})[0]
    assert missing("postgres:16", {"POSTGRES_PASSWORD": "  "})  # blank is not set
    assert missing("postgres:16", {"POSTGRES_PASSWORD": "s3cret"}) == []
    assert missing("postgres:16", {"POSTGRES_HOST_AUTH_METHOD": "trust"}) == []
    assert len(missing("mcr.microsoft.com/mssql/server:2022-latest", {})) == 2
    assert missing("nginx:latest", {}) == [] and missing("ghcr.io/x/postgres", {}) == []


def test_the_form_learns_which_variables_to_ask(client, auth_headers):
    headers = auth_headers("alice")
    r = client.get("/containers/image-env", params={"image": "postgres:16"}, headers=headers)
    assert r.status_code == 200
    first = r.json()["requises"][0][0]
    assert first["nom"] == "POSTGRES_PASSWORD" and first["secret"] and first["generer"]
    assert {v["nom"] for v in r.json()["utiles"]} >= {"POSTGRES_USER", "POSTGRES_DB"}
    assert client.get("/containers/image-env", params={"image": "nginx"}, headers=headers).json()["requises"] == []


# ---- Records that follow a rename ----


def test_a_renamed_vm_keeps_its_settings_and_history(database):
    from app.core import object_meta, renaming, vm_boot
    from app.core.vm_meta import get_vm_ssh_user, set_vm_ssh_user

    with database.get_conn() as db:
        db.execute("INSERT INTO pools (name) VALUES ('prod')")
        db.execute("INSERT INTO pool_members (pool_id, vm_name) VALUES (1, 'web')")
        db.execute(
            "INSERT INTO acl (subject_type, subject_id, role, resource_type, resource_id) "
            "VALUES ('user', 'bob', 'operateur', 'vm', 'web')"
        )
        db.execute(
            "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at) "
            "VALUES ('web', 'local', '<domain><name>web</name></domain>', 'admin', 'now')"
        )
        db.execute(
            "INSERT INTO backups (vm_name, chemin, mode, cree_le, statut) VALUES ('web', '/b/web.qcow2', 'froid', 'x', 'termine')"
        )
        db.execute(
            "INSERT INTO metrics_samples (ts, tier, scope, cible) VALUES ('t', 'raw', 'vm', 'web'), ('t', 'raw', 'vm', 'webx')"
        )
        db.execute(
            "INSERT INTO backup_group_jobs (nom, selection, exclues, frequence, heure, cible_dir, prochaine_execution) "
            "VALUES ('all', 'toutes', '[\"web\", \"db\"]', 'quotidien', '01:00', '/b', 'x')"
        )
        db.commit()
    set_vm_ssh_user("web", "nico")
    vm_boot.set_setting("web", True, 1, 0)
    object_meta.put("vm", "web", "front", ["prod"])

    renaming.vm_records("web", "shop")

    assert get_vm_ssh_user("shop") == "nico" and get_vm_ssh_user("web") is None
    assert vm_boot.get_setting("shop")["demarrage_auto"]
    assert object_meta.get("vm", "shop")["tags"] == ["prod"]
    with database.get_conn() as db:
        assert db.execute("SELECT vm_name FROM pool_members").fetchone()[0] == "shop"
        assert db.execute("SELECT resource_id FROM acl").fetchone()[0] == "shop"
        ha = db.execute("SELECT vm_name, domain_xml FROM ha_protected_vms").fetchone()
        assert ha["vm_name"] == "shop" and ET.fromstring(ha["domain_xml"]).findtext("name") == "shop"
        assert db.execute("SELECT vm_name FROM backups").fetchone()[0] == "shop"
        assert sorted(r[0] for r in db.execute("SELECT cible FROM metrics_samples")) == ["shop", "webx"]
        assert json.loads(db.execute("SELECT exclues FROM backup_group_jobs").fetchone()[0]) == ["db", "shop"]


def test_a_vm_of_a_remote_node_keeps_its_metrics(database):
    from app.core import renaming

    with database.get_conn() as db:
        db.execute("INSERT INTO metrics_samples (ts, tier, scope, cible) VALUES ('t', 'raw', 'vm', 'n2:web')")
        db.commit()
    renaming.vm_records("web", "shop", node="n2")
    with database.get_conn() as db:
        assert db.execute("SELECT cible FROM metrics_samples").fetchone()[0] == "n2:shop"


def test_a_renamed_container_keeps_its_records(database):
    from app.core import object_meta, renaming
    from app.core.container_meta import (
        get_container_app,
        get_container_ssh_user,
        get_container_storage,
        set_container_app,
        set_container_ssh_user,
        set_container_storage,
    )

    set_container_app("pg", "postgres:16", {"args": ["postgres"]}, "192.168.100.10", "isolated")
    set_container_storage("pg", "fast", "/data/hyperlite-containers")
    set_container_ssh_user("pg", "nico")
    object_meta.put("container", "pg", "database", [])
    renaming.container_records("pg", "db")
    assert get_container_app("db")["ip"] == "192.168.100.10" and get_container_app("pg") is None
    assert get_container_storage("db")["pool"] == "fast"
    assert get_container_ssh_user("db") == "nico"
    assert object_meta.get("container", "db")["notes"] == "database"


def test_a_renamed_node_keeps_its_vms_settings_and_metrics(database):
    from app.core import object_meta, renaming, vm_boot

    with database.get_conn() as db:
        db.execute("INSERT INTO nodes (name, hostname, added_at) VALUES ('n2', '10.0.0.2', 'now')")
        db.execute("INSERT INTO node_maintenance (node, started_by, started_at) VALUES ('n2', 'admin', 'now')")
        db.execute(
            "INSERT INTO metrics_samples (ts, tier, scope, cible) VALUES "
            "('t', 'raw', 'host', 'node:n2'), ('t', 'raw', 'vm', 'n2:web'), ('t', 'raw', 'vm', 'n22:web')"
        )
        db.commit()
    vm_boot.set_setting("web", True, 1, 0, node="n2")
    object_meta.put("node", "n2", "rack 3", [])
    object_meta.put("vm", "web", "", ["prod"], node="n2")

    renaming.node_records("n2", "rack3")

    assert vm_boot.get_setting("web", "rack3")["demarrage_auto"]
    assert object_meta.get("node", "rack3")["notes"] == "rack 3"
    assert object_meta.get("vm", "web", "rack3")["tags"] == ["prod"]
    with database.get_conn() as db:
        assert db.execute("SELECT name FROM nodes").fetchone()[0] == "rack3"
        assert db.execute("SELECT node FROM node_maintenance").fetchone()[0] == "rack3"
        cibles = sorted(r[0] for r in db.execute("SELECT cible FROM metrics_samples"))
        assert cibles == ["n22:web", "node:rack3", "rack3:web"]  # another node sharing a prefix is left alone


def test_the_reverse_key_follows_a_renamed_node(tmp_path, monkeypatch):
    from app.core import cluster

    keys = tmp_path / "reverse"
    keys.mkdir()
    (keys / "n2_ed25519").write_text("private")
    (keys / "n2_ed25519.pub").write_text("public")
    auth = tmp_path / "authorized_keys"
    auth.write_text(
        'from="10.0.0.2" ssh-ed25519 AAA hyperlite-reverse-n2\n'
        'from="10.0.0.3" ssh-ed25519 BBB hyperlite-reverse-n22\n'
        "ssh-ed25519 CCC admin@laptop"
    )
    monkeypatch.setattr(cluster, "REVERSE_KEY_DIR", keys)
    monkeypatch.setattr(cluster, "_authorized_keys_path", lambda: auth)
    cluster.rename_reverse_trust("n2", "rack3")
    assert (keys / "rack3_ed25519").read_text() == "private" and not (keys / "n2_ed25519").exists()
    lines = auth.read_text().splitlines()
    assert lines[0].endswith(" hyperlite-reverse-rack3")
    assert lines[1].endswith(" hyperlite-reverse-n22") and lines[2] == "ssh-ed25519 CCC admin@laptop"


# ---- Routes ----


class _Domain:
    def listAllCheckpoints(self):
        return []  # no replication checkpoint (app/core/checkpoints.py)

    def __init__(self, name, active=False, snapshots=0, xml=None, app=False):
        self._name, self.active, self.snapshots = name, active, snapshots
        self.xml = xml or f"<domain><name>{name}</name><devices/></domain>"
        self.renamed_to = None
        self.shutdown_flags = None

    def name(self):
        return self._name

    def isActive(self):
        return self.active

    def snapshotNum(self, flags=0):
        return self.snapshots

    def XMLDesc(self, flags=0):
        return self.xml

    def rename(self, new, flags=0):
        self.renamed_to = new

    def ID(self):
        return 4242 if self.active else -1

    def shutdownFlags(self, flags):
        self.shutdown_flags = flags

    def shutdown(self):
        self.shutdown_flags = "default"


class _Conn:
    def __init__(self, *domains):
        self.domains = {d.name(): d for d in domains}
        self.defined = []

    def lookupByName(self, name):
        if name in self.domains:
            return self.domains[name]
        for d in self.domains.values():
            if d.renamed_to == name:
                return d
        raise _missing()

    def defineXML(self, xml):
        self.defined.append(xml)
        return _Domain(ET.fromstring(xml).findtext("name"), xml=xml)

    def close(self):
        pass


@pytest.fixture()
def vm_routes(monkeypatch):
    from app.routers.vms import rename

    def install(conn):
        monkeypatch.setattr(rename, "open_conn", lambda node=None: conn)
        monkeypatch.setattr(rename, "_domain_summary", lambda d: {"nom": d.renamed_to or d.name()})
        monkeypatch.setattr(rename, "refresh_pools_for_paths", lambda conn, paths: None)
        return conn

    return install


def test_a_vm_is_renamed_only_when_stopped_and_without_snapshots(client, auth_headers, vm_routes):
    headers = auth_headers("alice")
    vm_routes(_Conn(_Domain("up", active=True), _Domain("snap", snapshots=2), _Domain("taken"), _Domain("web")))
    post = lambda name, new: client.post(f"/vms/{name}/rename", json={"new_name": new}, headers=headers)  # noqa: E731
    assert post("up", "up2").status_code == 409
    assert "snapshots" in post("snap", "snap2").json()["detail"]
    assert post("web", "taken").status_code == 409
    assert post("web", "bad name!").status_code == 422
    assert post("web", "web").status_code == 422
    assert post("nope", "nope2").status_code == 404
    r = post("web", "shop")
    assert r.status_code == 200 and r.json()["nom"] == "shop"


def test_renaming_a_vm_is_for_administrators(client, auth_headers, vm_routes):
    vm_routes(_Conn(_Domain("web")))
    r = client.post("/vms/web/rename", json={"new_name": "shop"}, headers=auth_headers("olga", role="observateur"))
    assert r.status_code == 403


def test_a_kubernetes_vm_keeps_its_name(client, auth_headers, vm_routes, database):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO k8s_clusters (nom, reseau, serveur, workers, vcpu, memoire_mo, disque_go, statut, cree_le) "
            "VALUES ('k', 'default', 'k-server', '[\"k-worker-1\"]', 2, 2048, 20, 'pret', 'now')"
        )
        db.commit()
    vm_routes(_Conn(_Domain("k-worker-1")))
    r = client.post("/vms/k-worker-1/rename", json={"new_name": "w1"}, headers=auth_headers("alice"))
    assert r.status_code == 409 and "Kubernetes" in r.json()["detail"]


def test_the_vm_own_files_take_its_new_name(tmp_path, monkeypatch):
    from app.routers.vms import rename

    monkeypatch.setattr(rename, "NVRAM_DIR", str(tmp_path / "nvram"))
    monkeypatch.setattr(rename, "refresh_pools_for_paths", lambda conn, paths: None)
    (tmp_path / "nvram").mkdir()
    (tmp_path / "web-cloudinit.iso").write_text("seed")
    (tmp_path / "debian.iso").write_text("library")
    (tmp_path / "nvram" / "web_VARS.fd").write_text("vars")
    xml = f"""<domain><name>web</name><os><nvram>{tmp_path}/nvram/web_VARS.fd</nvram></os><devices>
      <disk device='cdrom'><source file='{tmp_path}/web-cloudinit.iso'/></disk>
      <disk device='cdrom'><source file='{tmp_path}/debian.iso'/></disk></devices></domain>"""
    conn = _Conn()
    rename._rename_own_files(conn, _Domain("web", xml=xml), "web", "shop", None)
    assert (tmp_path / "shop-cloudinit.iso").read_text() == "seed"
    assert (tmp_path / "debian.iso").exists()  # a library ISO is never renamed
    assert (tmp_path / "nvram" / "shop_VARS.fd").read_text() == "vars"
    defined = ET.fromstring(conn.defined[-1])
    assert defined.findtext("os/nvram").endswith("/shop_VARS.fd")
    assert [s.get("file") for s in defined.iter("source")] == [
        f"{tmp_path}/shop-cloudinit.iso",
        f"{tmp_path}/debian.iso",
    ]


def test_an_application_container_is_stopped_with_sigterm(client, auth_headers, monkeypatch):
    from app.core.container_meta import set_container_app
    from app.routers import containers

    domain = _Domain("pg", active=True)
    monkeypatch.setattr(containers, "open_lxc_conn", lambda: _Conn(domain))
    started = []
    monkeypatch.setattr(
        containers.threading, "Thread", lambda **kw: type("T", (), {"start": lambda s: started.append(kw["args"])})()
    )
    set_container_app("pg", "postgres:16", {"args": ["postgres"]}, "192.168.100.10", "isolated")
    r = client.post("/containers/pg/stop", headers=auth_headers("alice"))
    assert r.status_code == 200 and r.json()["arret_force_apres_s"] == containers.STOP_GRACE_S["application"]
    assert domain.shutdown_flags == libvirt.VIR_DOMAIN_SHUTDOWN_SIGNAL
    assert started == [("pg", 4242, containers.STOP_GRACE_S["application"])]  # forced after the grace period


def test_the_forced_stop_leaves_a_container_restarted_meanwhile(monkeypatch):
    from app.routers import containers

    domain = _Domain("pg", active=True)
    destroyed = []
    domain.destroy = lambda: destroyed.append(True)
    monkeypatch.setattr(containers, "open_lxc_conn", lambda: _Conn(domain))
    monkeypatch.setattr(containers.time, "sleep", lambda s: None)
    containers._force_stop_later("pg", 1111, 0.01)  # another run: ID 4242
    assert destroyed == []
    containers._force_stop_later("pg", 4242, 0.01)
    assert destroyed == [True]


def test_a_container_rename_moves_its_filesystem(client, auth_headers, monkeypatch, tmp_path):
    from app.core import container_builder
    from app.routers import containers

    monkeypatch.setattr(container_builder, "CONTAINERS_DIR", tmp_path)
    rootfs = tmp_path / "web"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "etc" / "hostname").write_text("web\n")
    (rootfs / "etc" / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\tweb\n")
    xml = f"<domain type='lxc'><name>web</name><devices><filesystem><source dir='{rootfs}'/></filesystem></devices></domain>"
    domain = _Domain("web", xml=xml)
    domain.autostart = lambda: 1
    domain.undefine = lambda: None
    conn = _Conn(domain)
    monkeypatch.setattr(containers, "open_lxc_conn", lambda: conn)
    monkeypatch.setattr(containers, "_summary", lambda d: {"nom": d.name()})
    autostart = []
    monkeypatch.setattr(_Domain, "setAutostart", lambda self, v: autostart.append(v), raising=False)
    headers = auth_headers("alice")

    r = client.post("/containers/web/rename", json={"new_name": "shop"}, headers=headers)
    assert r.status_code == 200 and r.json()["nom"] == "shop"
    assert not rootfs.exists() and (tmp_path / "shop" / "etc" / "hostname").read_text() == "shop\n"
    assert "127.0.1.1\tshop" in (tmp_path / "shop" / "etc" / "hosts").read_text()
    assert ET.fromstring(conn.defined[-1]).find("devices/filesystem/source").get("dir") == str(tmp_path / "shop")
    assert autostart == [1]


def test_a_running_container_is_not_renamed(client, auth_headers, monkeypatch):
    from app.routers import containers

    monkeypatch.setattr(containers, "open_lxc_conn", lambda: _Conn(_Domain("web", active=True)))
    r = client.post("/containers/web/rename", json={"new_name": "shop"}, headers=auth_headers("alice"))
    assert r.status_code == 409


def test_the_hostname_is_never_written_through_a_link(tmp_path):
    from app.core.container_builder import set_container_hostname

    outside = tmp_path / "host-hostname"
    outside.write_text("host\n")
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "etc" / "hostname").symlink_to(outside)
    (rootfs / "etc" / "hosts").symlink_to(outside)
    set_container_hostname(rootfs, "shop")
    assert outside.read_text() == "host\n"


# ---- Root shell ----


def test_the_shell_enters_the_container_init(tmp_path):
    from app.core import container_shell

    proc = tmp_path / "proc"
    (proc / "100" / "task" / "100").mkdir(parents=True)
    (proc / "100" / "task" / "100" / "children").write_text("101 102 ")
    for pid, nspid in (("101", "101"), ("102", "102\t1")):
        (proc / pid).mkdir()
        (proc / pid / "status").write_text(f"Name:\tx\nNSpid:\t{nspid}\n")
    assert container_shell.init_pid(100, proc) == 102
    assert container_shell.init_pid(999, proc) is None
    argv = container_shell.command(102)
    assert argv[:2] == ["nsenter", "--target=102"] and "--mount" in argv and "--pid" in argv
    env = container_shell.environment({"env": {"PATH": "/usr/lib/postgresql/16/bin:/usr/bin"}})
    assert env["PATH"].startswith("/usr/lib/postgresql") and env["TERM"] == "xterm-256color"


def test_the_container_shell_is_for_administrators(client, auth_headers, monkeypatch):
    from app.routers import containers

    monkeypatch.setattr(containers, "open_lxc_conn", lambda: _Conn(_Domain("web", active=True)))
    r = client.post("/containers/web/shell-ticket", headers=auth_headers("olga", role="observateur"))
    assert r.status_code == 403


def test_files_of_a_remote_vm_are_renamed_with_quoted_paths(monkeypatch):
    from app.routers.vms import rename

    calls = []
    monkeypatch.setattr(rename, "get_node", lambda name: {"name": name})
    monkeypatch.setattr(
        rename, "_run_ssh", lambda node, args: calls.append(args) or type("R", (), {"returncode": 0, "stderr": ""})()
    )
    files = rename._Files("n2")
    files.rename("/var/lib/libvirt/images/a b;x-cloudinit.iso", "/var/lib/libvirt/images/shop-cloudinit.iso")
    assert calls == [
        [
            "mv",
            "-n",
            "--",
            "'/var/lib/libvirt/images/a b;x-cloudinit.iso'",
            "/var/lib/libvirt/images/shop-cloudinit.iso",
        ]
    ]
