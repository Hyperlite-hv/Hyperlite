"""The API reads libvirt through its read-only socket (docs/design/privilege-separation.md, 4e, points 10, 12 and 18 of
#298): open_conn and the LXC connections honour `readonly` on this node, as polkit refuses www-data the read-write
socket, and what changes a guest goes through hyperlite-daemon. The scan at the end keeps the routes so."""

import ast
from pathlib import Path

import pytest

from app.core import libvirt_utils

ROOT = Path(__file__).resolve().parents[1]


class _Pool:
    def __init__(self, conn, active=True, autostart=True):
        self.conn, self.active, self.on = conn, active, autostart

    def isActive(self):
        return self.active

    def autostart(self):
        return self.on

    def build(self):
        self.conn.wrote.append("build")

    def create(self):
        self.conn.wrote.append("create")
        self.active = True

    def setAutostart(self, on):
        self.conn.wrote.append("setAutostart")
        self.on = on


class _Conn:
    """A libvirt connection with or without the 'default' pool: what was written through it."""

    def __init__(self, pool=None):
        self.pool, self.wrote = pool, []

    def storagePoolLookupByName(self, name):
        if self.pool is None:
            raise libvirt_utils.libvirt.libvirtError("Storage pool not found")
        return self.pool

    def storagePoolDefineXML(self, xml):
        self.wrote.append("define")
        self.pool = _Pool(self, active=False, autostart=False)
        return self.pool


class _Libvirt:
    """Records which of libvirt's sockets each connection opened."""

    def __init__(self, monkeypatch):
        self.opened = []
        monkeypatch.setattr(libvirt_utils.libvirt, "open", lambda uri: self._open("rw", uri))
        monkeypatch.setattr(libvirt_utils.libvirt, "openReadOnly", lambda uri: self._open("ro", uri))

    def _open(self, how, uri):
        self.opened.append((how, uri))
        return _Conn()


def test_this_nodes_libvirt_is_opened_read_only_when_asked(monkeypatch):
    seen = _Libvirt(monkeypatch)
    libvirt_utils.open_conn(readonly=True)
    libvirt_utils.open_conn()
    libvirt_utils.open_lxc_conn(readonly=True)
    libvirt_utils.open_lxc_conn_if_any(readonly=True)
    libvirt_utils.open_lxc_conn()
    assert seen.opened == [
        ("ro", libvirt_utils.LIBVIRT_URI),
        ("rw", libvirt_utils.LIBVIRT_URI),
        ("ro", libvirt_utils.LXC_URI),
        ("ro", libvirt_utils.LXC_URI),
        ("rw", libvirt_utils.LXC_URI),
    ]


def test_enabling_ha_turns_libvirts_autostart_off_through_the_daemon(monkeypatch):
    from app.core import ha, privileged

    asked = []
    monkeypatch.setattr(privileged, "run", lambda op, **args: asked.append((op, args)))
    ha._autostart_off("web", None, "ana")
    assert asked == [("vm.autostart", {"user": "ana", "vm": "web", "autostart": False, "node": None})]


def test_a_daemon_that_refuses_the_autostart_leaves_the_protection_to_be_recorded(monkeypatch, caplog):
    from app.core import ha, privileged
    from app.daemon.registry import DaemonError

    def refuse(op, **args):
        raise DaemonError("forbidden", "no")

    monkeypatch.setattr(privileged, "run", refuse)
    ha._autostart_off("web", "n2", "ana")
    assert "autostart not turned off: no" in caplog.text


# Routes that still open a connection that may write, each with why. The migration's compatibility check reads the
# migratable (secure) definition, which a read-only connection refuses: it moves into a daemon operation with point 11
# of #298, and these go with it.
ALLOWED = {
    ("app/routers/vms/migration.py", "_source_conn"): "the compatibility check, point 11",
    ("app/routers/nodes.py", "_compat_with_local"): "the compatibility check, point 11",
    ("app/routers/nodes.py", "_plan"): "the drain plan's compatibility check, point 11",
    ("app/routers/nodes.py", "_spread_plan"): "the drain plan's compatibility check, point 11",
}
OPENERS = ("open_conn", "open_lxc_conn", "open_lxc_conn_if_any")


class _Opens(ast.NodeVisitor):
    def __init__(self, rel, found):
        self.rel, self.found, self.stack = rel, found, []

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        name = ast.unparse(node.func).split(".")[-1]
        if name in OPENERS:
            readonly = next((k.value for k in node.keywords if k.arg == "readonly"), None)
            if not (isinstance(readonly, ast.Constant) and readonly.value is True):
                self.found.add((self.rel, self.stack[-1] if self.stack else "<module>"))
        self.generic_visit(node)


@pytest.mark.parametrize("folder", ["app/routers"])
def test_the_routes_read_libvirt_through_its_read_only_socket(folder):
    found = set()
    for path in sorted((ROOT / folder).rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        _Opens(rel, found).visit(ast.parse(path.read_text(), rel))
    assert sorted(found - set(ALLOWED)) == [], "open it with readonly=True, or go through a daemon operation"
    assert sorted(set(ALLOWED) - found) == [], "an entry of ALLOWED opens read-only now: remove it"


def test_the_default_pool_already_there_is_read_and_never_written_again():
    conn = _Conn()
    conn.pool = _Pool(conn)
    assert libvirt_utils.lookup_pool(conn, "default") is conn.pool
    assert conn.wrote == []  # its autostart was set again at every lookup, a write the read-only socket refuses
    conn.pool.on = False
    libvirt_utils.ensure_default_pool(conn)
    assert conn.wrote == ["setAutostart"]


@pytest.mark.parametrize("node", [None, "hv2"])
def test_through_a_read_only_connection_the_daemon_makes_the_default_pool(monkeypatch, node):
    from app.core import node_tunnel, privileged

    seen = _Libvirt(monkeypatch)
    monkeypatch.setattr(libvirt_utils, "_through_tunnel", lambda name: seen._open("ro", node_tunnel.uri(name)))
    monkeypatch.setattr("app.core.cluster.get_node", lambda name: {"name": name})
    monkeypatch.setattr("app.core.node_health.check", lambda row: None)
    conn = libvirt_utils.open_conn(node, readonly=True)
    asked = []

    def daemon(op, **args):
        asked.append((op, args))
        conn.pool = _Pool(conn)

    monkeypatch.setattr(privileged, "run", daemon)
    assert libvirt_utils.lookup_pool(conn, "default") is conn.pool
    assert asked == [("storage.ensure_default_pool", {"node": node})] and conn.wrote == []
    asked.clear()
    libvirt_utils.lookup_pool(conn, "default")  # there now: read only
    assert asked == []


def test_a_read_write_connection_still_makes_the_default_pool_itself():
    conn = _Conn()  # the daemon's own (libvirt_utils.local_conn)
    libvirt_utils.ensure_default_pool(conn)
    assert conn.wrote == ["define", "build", "create", "setAutostart"]
