"""Application containers: a Docker image's own process, run by libvirt's LXC driver instead of systemd.

The image pull and libvirt are replaced by fakes: what is tested is how the image's configuration becomes the
container's command, environment and address, and the checks around it.
"""

import json
import xml.etree.ElementTree as ET

import pytest

NGINX_CONFIG = {
    "process": {
        "args": ["/docker-entrypoint.sh", "nginx", "-g", "daemon off;"],
        "env": [
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "NGINX_VERSION=1.31.6",
            "bad name=x",
        ],
        "cwd": "/",
        "user": {"uid": 0, "gid": 0},
    }
}


@pytest.fixture()
def image(tmp_path, monkeypatch):
    from app.core import container_builder

    monkeypatch.setattr(container_builder, "PULLED_IMAGES_DIR", tmp_path / "images")

    def make(ref, config, files=("docker-entrypoint.sh",)):
        cache = container_builder._image_cache_dir(ref)
        rootfs = cache / "rootfs"
        (rootfs / "usr" / "bin").mkdir(parents=True)
        (rootfs / "etc").mkdir()
        for f in files:
            (rootfs / "usr" / "bin" / f).write_text("#!/bin/sh\n")
        (cache / "config.json").write_text(json.dumps(config))
        return rootfs

    return make


def test_the_image_configuration_becomes_the_process_settings(image):
    from app.core.container_builder import app_spec, read_image_config

    image("nginx:latest", NGINX_CONFIG)
    spec = read_image_config("nginx:latest")
    assert spec["args"] == ["/docker-entrypoint.sh", "nginx", "-g", "daemon off;"]
    assert spec["env"]["NGINX_VERSION"] == "1.31.6" and "bad name" not in spec["env"]
    overridden = app_spec("nginx:latest", command=["nginx", "-T"], env={"TZ": "Europe/Paris", "NGINX_VERSION": "x"})
    assert overridden["args"] == ["nginx", "-T"]
    assert overridden["env"]["TZ"] == "Europe/Paris" and overridden["env"]["NGINX_VERSION"] == "x"


def test_an_image_without_a_command_needs_one(image):
    from app.core.container_builder import app_spec

    image("scratchy:1", {"process": {"args": [], "env": []}})
    with pytest.raises(ValueError, match="defines no command"):
        app_spec("scratchy:1")
    assert app_spec("scratchy:1", command=["/app"])["env"]["PATH"]  # a default PATH is always given


def test_a_bare_program_name_is_found_in_the_image_path_only(image, tmp_path):
    from app.core.container_builder import resolve_init

    rootfs = image("busybox:latest", {"process": {"args": ["sh"], "env": ["PATH=/usr/bin:/bin"]}}, files=("sh",))
    spec = {"args": ["sh"], "env": {"PATH": "/usr/bin:/bin"}}
    assert resolve_init(rootfs, spec) == "/usr/bin/sh"
    assert resolve_init(rootfs, {"args": ["/abs/prog"], "env": {}}) == "/abs/prog"
    with pytest.raises(ValueError, match="not found in the image"):
        resolve_init(rootfs, {"args": ["missing"], "env": {"PATH": "/usr/bin"}})
    # a relative PATH entry is ignored: it would be resolved against the host's working directory
    with pytest.raises(ValueError):
        resolve_init(rootfs, {"args": ["sh"], "env": {"PATH": "usr/bin"}})


def test_the_program_lookup_never_leaves_the_image(image, tmp_path):
    from app.core.container_builder import resolve_init

    rootfs = image("busybox:latest", {"process": {"args": ["sh"]}}, files=("sh",))
    (tmp_path / "host-secret").write_text("x")  # exists on the host, outside the rootfs
    for program, path in (("../../host-secret", "/usr/bin"), ("host-secret", "/../../../.."), ("x/sh", "/usr")):
        with pytest.raises(ValueError):
            resolve_init(rootfs, {"args": [program], "env": {"PATH": path}})


def test_hostname_and_resolver_never_follow_a_link_out_of_the_container(tmp_path):
    from app.core.container_builder import prepare_app_rootfs

    outside = tmp_path / "host-file"
    outside.write_text("untouched")
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "etc" / "resolv.conf").symlink_to(outside)
    prepare_app_rootfs(rootfs, "web", "192.168.100.1")
    assert outside.read_text() == "untouched"
    assert (rootfs / "etc" / "resolv.conf").read_text() == "nameserver 192.168.100.1\n"
    assert "web" in (rootfs / "etc" / "hosts").read_text()


def test_the_domain_carries_the_process_and_the_address_escaped(tmp_path):
    from app.core.container_builder import build_container_xml

    spec = {
        "args": ["/bin/sh", "-c", "echo '<x>' && echo \"&\""],
        "env": {"A": '1 < 2 & "q"'},
        "cwd": "/srv",
        "uid": 101,
        "gid": 101,
    }
    app = {"init": "/bin/sh", "spec": spec, "ip": "192.168.100.10", "prefix": 24, "gateway": "192.168.100.1"}
    root = ET.fromstring(build_container_xml("web", 1, 256, tmp_path, network="lan", mac="52:54:00:00:00:01", app=app))
    os_el = root.find("os")
    assert os_el.findtext("init") == "/bin/sh"
    assert [a.text for a in os_el.findall("initarg")] == ["-c", "echo '<x>' && echo \"&\""]
    assert os_el.find("initenv").get("name") == "A" and os_el.findtext("initenv") == '1 < 2 & "q"'
    assert os_el.findtext("initdir") == "/srv" and os_el.findtext("inituser") == "101"
    iface = root.find("devices/interface")
    assert iface.find("ip").get("address") == "192.168.100.10" and iface.find("ip").get("prefix") == "24"
    assert iface.find("route").get("gateway") == "192.168.100.1"
    # a system container keeps /sbin/init and DHCP
    plain = ET.fromstring(build_container_xml("sys", 1, 256, tmp_path))
    assert plain.findtext("os/init") == "/sbin/init" and plain.find("devices/interface/ip") is None


@pytest.mark.parametrize(
    ("command", "env", "ok"),
    [
        (None, None, True),
        (["nginx", "-g", "daemon off;"], {"TZ": "UTC"}, True),
        ([], None, False),
        (["a\0b"], None, False),
        (None, {"1BAD": "x"}, False),
        (None, {"GOOD": "a\0"}, False),
    ],
)
def test_user_overrides_are_checked(command, env, ok):
    from app.core.container_builder import validate_app_overrides

    assert (validate_app_overrides(command, env) == []) is ok


# ---------------------------------------------------------------- API


class _Net:
    def __init__(self, active=True, subnet=True):
        self.active, self.subnet = active, subnet

    def isActive(self):
        return self.active

    def XMLDesc(self, flags=0):
        ip = "<ip address='192.168.100.1' netmask='255.255.255.0'><dhcp><range start='192.168.100.10' end='192.168.100.100'/></dhcp></ip>"
        return f"<network><name>n</name>{ip if self.subnet else ''}</network>"


class _Domain:
    def __init__(self, conn, xml):
        self.conn, self.xml, self.active = conn, xml, False

    def name(self):
        return ET.fromstring(self.xml).findtext("name")

    def XMLDesc(self, flags=0):
        return self.xml

    def create(self):
        self.active = True

    def destroy(self):
        self.active = False

    def undefine(self):
        self.conn.domains.pop(self.name())

    def isActive(self):
        return self.active

    def info(self):
        return [1 if self.active else 5, 262144, 262144, 1, 0]

    def ID(self):
        return 7

    def UUIDString(self):
        return "u"

    def interfaceAddresses(self, source):
        return {}


class _Conn:
    def __init__(self):
        self.domains = {}
        self.networks = {"lan": _Net(), "down": _Net(active=False), "br": _Net(subnet=False)}

    def lookupByName(self, name):
        import libvirt

        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def networkLookupByName(self, name):
        import libvirt

        if name not in self.networks:
            raise libvirt.libvirtError("no network")
        return self.networks[name]

    def defineXML(self, xml):
        d = _Domain(self, xml)
        self.domains[d.name()] = d
        return d

    def getHostname(self):
        return "host"

    def listAllDomains(self):
        return list(self.domains.values())

    def close(self):
        pass


@pytest.fixture()
def api(database, image, monkeypatch, tmp_path):
    from app.routers import containers

    conn = _Conn()
    reserved = {}
    monkeypatch.setattr(containers, "open_lxc_conn", lambda: conn)
    monkeypatch.setattr(containers, "generate_mac", lambda c: f"52:54:00:00:00:{len(reserved) + 1:02x}")

    def allocate(c, network, mac):
        reserved[mac] = f"192.168.100.{10 + len(reserved)}"
        return reserved[mac]

    monkeypatch.setattr(containers, "allocate_static_ip", allocate)
    monkeypatch.setattr(containers, "release_static_ip", lambda c, network, mac: reserved.pop(mac, None))
    rootfs = image("nginx:latest", NGINX_CONFIG)
    pulls = []

    def create_rootfs(name, image=None, bootstrap=True, storage=None):
        pulls.append((name, image, bootstrap))
        return rootfs, None

    monkeypatch.setattr(containers, "create_container_rootfs", create_rootfs)
    monkeypatch.setattr(containers, "delete_container_rootfs", lambda name: None)
    return {"conn": conn, "reserved": reserved, "pulls": pulls}


def test_an_image_runs_its_own_process_with_a_fixed_address(api, client, auth_headers):
    admin = auth_headers("admin")
    r = client.post(
        "/containers",
        json={"name": "web", "image": "nginx:latest", "network": "lan", "env": {"TZ": "UTC"}},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mode"] == "application" and body["image"] == "nginx:latest" and body["ip"] == "192.168.100.10"
    assert api["pulls"] == [("web", "nginx:latest", False)]  # no systemd/sshd bootstrap
    xml = ET.fromstring(api["conn"].domains["web"].xml)
    # the launcher runs first, sends the output to the log, then execs the image's entrypoint
    assert xml.findtext("os/init") == "/.hyperlite/hl-console"
    assert [a.text for a in xml.findall("os/initarg")][:2] == ["/.hyperlite/console.log", "/docker-entrypoint.sh"]
    assert {e.get("name"): e.text for e in xml.findall("os/initenv")}["TZ"] == "UTC"

    ticket = client.post("/containers/web/terminal-ticket", headers=admin)
    assert ticket.status_code == 409 and "no SSH server" in ticket.text

    assert client.delete("/containers/web", headers=admin).status_code == 200
    assert api["reserved"] == {}  # the address goes back to the network
    assert client.get("/containers", headers=admin).json() == []


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"name": "web", "mode": "application", "network": "lan"}, "needs an image"),
        ({"name": "web", "image": "nginx:latest", "network": "down"}, "is not started"),
        ({"name": "web", "image": "nginx:latest", "network": "br"}, "NAT or isolated"),
        ({"name": "web", "image": "nginx:latest", "network": "ghost"}, "not found"),
        ({"name": "web", "image": "nginx:latest", "network": "lan", "env": {"1X": "y"}}, "Environment variable"),
        ({"name": "web", "mode": "systeme", "network": "lan", "command": ["x"]}, "application containers only"),
        ({"name": "web", "mode": "systeme", "network": "lan", "username": "demo"}, "password"),
    ],
)
def test_requests_are_refused_before_any_download(api, client, auth_headers, payload, detail):
    r = client.post("/containers", json=payload, headers=auth_headers("admin"))
    assert r.status_code == 422, r.text
    assert detail in r.text
    assert api["pulls"] == [] and api["reserved"] == {}


def test_a_system_container_from_an_image_is_still_available(api, client, auth_headers, monkeypatch):
    from app.routers import containers

    monkeypatch.setattr(containers, "configure_container_rootfs", lambda *a, **k: None)
    monkeypatch.setattr(containers, "get_or_create_automation_pubkey", lambda: "ssh-ed25519 AAAA test")
    r = client.post(
        "/containers",
        json={
            "name": "box",
            "image": "alpine:3.20",
            "mode": "systeme",
            "network": "lan",
            "username": "demo",
            "password": "long-enough",
        },
        headers=auth_headers("admin"),
    )
    assert r.status_code == 201, r.text
    assert r.json()["mode"] == "systeme"
    assert api["pulls"] == [("box", "alpine:3.20", True)]
    assert ET.fromstring(api["conn"].domains["box"].xml).findtext("os/init") == "/sbin/init"
