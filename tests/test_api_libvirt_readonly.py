"""The API reads libvirt through its read-only socket (docs/design/privilege-separation.md, 4e, points 10, 12 and 18 of
#298): open_conn and the LXC connections honour `readonly` on this node, as polkit refuses www-data the read-write
socket, and what changes a guest goes through hyperlite-daemon. The scan at the end keeps the routes so."""

import ast
from pathlib import Path

import pytest

from app.core import libvirt_utils

ROOT = Path(__file__).resolve().parents[1]


class _Libvirt:
    """Records which of libvirt's sockets each connection opened."""

    def __init__(self, monkeypatch):
        self.opened = []
        monkeypatch.setattr(libvirt_utils.libvirt, "open", lambda uri: self._open("rw", uri))
        monkeypatch.setattr(libvirt_utils.libvirt, "openReadOnly", lambda uri: self._open("ro", uri))

    def _open(self, how, uri):
        self.opened.append((how, uri))
        return object()


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
