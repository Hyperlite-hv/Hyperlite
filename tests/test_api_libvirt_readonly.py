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


# Routes that still open a connection that may write, each with why. None since the migration's compatibility check,
# which reads the migratable (secure) definition, moved into hyperlite-daemon (point 11 of #298).
ALLOWED = {}
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


class _Domain:
    def name(self):
        return "web"

    def isActive(self):
        return True

    def XMLDesc(self, flags=0):
        return "<domain><name>web</name><devices/></domain>"


class _Host:
    def __init__(self, *vms):
        self.vms = vms

    def lookupByName(self, name):
        if name not in self.vms:
            raise libvirt_utils.libvirt.libvirtError("no domain")
        return _Domain()

    def listAllDomains(self, flags=0):
        return [_Domain() for _ in self.vms]


def _www_data(monkeypatch, daemon_libvirt):
    """The API as www-data, once its accounts signed in: the daemon's operations go to its socket, stood in for here
    (from uid 33, with the request's proof), and the daemon opens both nodes' libvirt."""
    import app.daemon.ops  # noqa: F401  (registers the operations)
    from app.core import cluster_compat, privileged
    from app.daemon.registry import Context, DaemonError, dispatch

    asked = []

    def call(op, args=None, user=None, timeout=None):
        asked.append(op)
        proof = privileged.daemon_client._proof()
        answer = dispatch({"op": op, "args": args or {}}, Context(user, None, 1, 33, proof=proof))
        if not answer.get("ok"):
            raise DaemonError(answer["error"], answer.get("detail"))
        return answer["result"], []

    daemon_libvirt(_Host("web"), nodes={"n2": _Host()})
    monkeypatch.setattr(cluster_compat, "check_vm_migration", lambda src, dst, domain: [])
    monkeypatch.setattr(cluster_compat, "check_pair", lambda src, dst: [])
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.setattr(privileged.daemon_client, "call_fds", call)
    return asked


def _node(database, name):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, status, added_at) "
            "VALUES (?, ?, 'root', 22, 'online', '2026-01-01T00:00:00+00:00')",
            (name, f"{name}.example"),
        )
        db.commit()


def test_the_migrations_checks_are_the_daemons(client, auth_headers, database, monkeypatch, daemon_libvirt):
    """They read the VM's migratable definition, which the read-only socket refuses (point 11 of #298)."""
    _node(database, "n2")
    admin, viewer = auth_headers("root"), auth_headers("vic", role="viewer")
    from_www_data = _www_data(monkeypatch, daemon_libvirt)
    r = client.get("/vms/web/migration-check", params={"target_node": "n2"}, headers=admin)
    assert r.status_code == 200 and r.json()["checks"] == [], r.text
    assert client.get("/vms/ghost/migration-check", params={"target_node": "n2"}, headers=admin).status_code == 404
    assert client.get("/nodes/n2/compatibility", headers=admin).status_code == 200
    plan = client.get("/nodes/local/drain-plan", params={"target_node": "n2"}, headers=admin)
    assert plan.status_code == 200 and plan.json()["migratable"] == ["web"], plan.text
    assert from_www_data == ["vm.migration_check", "vm.migration_check", "node.compatibility", "node.vm_plan"]
    from_www_data.clear()
    assert client.get("/vms/web/migration-check", params={"target_node": "n2"}, headers=viewer).status_code == 403
    assert from_www_data == []  # the route's own check first; the daemon's needs the same (ADMIN)


@pytest.mark.parametrize("op", ["vm.migration_check", "node.compatibility", "node.vm_plan"])
def test_the_daemon_runs_them_for_an_administrator_only(op, auth_headers):
    import app.daemon.ops  # noqa: F401  (registers the operations)
    from app.daemon.registry import Context, dispatch

    token = auth_headers("vic", role="viewer")["Authorization"].split()[1]
    args = {"vm.migration_check": {"vm": "web", "target": "n2"}, "node.vm_plan": {"source": "local"}}
    answer = dispatch({"op": op, "args": args.get(op, {"node": "n2"})}, Context(None, None, 1, 33, proof=token))
    assert answer["error"] == "forbidden"


def test_the_rolling_updates_plan_of_a_nodes_vms_is_the_daemons(database, auth_headers, monkeypatch, daemon_libvirt):
    from app.core import peer_auth, rolling_update

    _node(database, "n2")
    token = auth_headers("root")["Authorization"].split()[1]
    from_www_data = _www_data(monkeypatch, daemon_libvirt)
    proof = peer_auth.CREDENTIAL.set(token)
    try:
        out = rolling_update.node_plan(None, "root", lambda: ({"verifiable": True, "system": {}}, "stable"), "n2")
    finally:
        peer_auth.CREDENTIAL.reset(proof)
    assert out["vms_to_migrate"] == ["web"] and out["reachable"], out
    assert "node.vm_plan" in from_www_data
