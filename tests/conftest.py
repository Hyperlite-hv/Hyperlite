"""Shared pytest fixtures.

Secrets are read at import time by the application, so they are set here
before any `app.*` module is imported. Nothing in the test suite may write
to the repository (in particular no `.env`, no `hyperlite.db`).
"""

import contextlib
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet

os.environ.setdefault("HYPERLITE_SECRET_KEY", "test-only-secret-key-0123456789abcdef0123456789abcdef")
os.environ.setdefault("HYPERLITE_ENCRYPTION_KEY", Fernet.generate_key().decode())
# The name shared tables store for this node (app/core/self_node.py): fixed, so rows and cfs paths do not depend on the
# host the suite runs on. Tests of the name itself remove it.
os.environ.setdefault("HYPERLITE_NODE_NAME", "hv-test")
# Never the host's own hyperlite-cfs: on a machine running Hyperlite, its socket (/run/hyperlite-cfs/socket) is reachable
# by a suite run as root, and a test that reached it would write into that machine's real configuration. A socket that
# cannot exist; tests of the daemon start their own and point this variable at it.
os.environ["HYPERLITE_CFS_SOCKET"] = "/nonexistent/hyperlite-cfs-tests/socket"
# Never the host's own watchdog either: armed and left unfed, it would reset the machine running the suite.
os.environ["HYPERLITE_WATCHDOG_DEVICE"] = "/nonexistent/hyperlite-watchdog-tests"
# Nor the host's initial admin password files (app/core/initial_password.py): a password change in a test removes them.
os.environ["HYPERLITE_INITIAL_PASSWORD_ROOT"] = "/nonexistent/hyperlite-initial-password-tests"
# Nor /var/log/hyperlite: the access log of a test client goes to a directory of its own (app/core/access_log.py).
os.environ["HYPERLITE_ACCESS_LOG_DIR"] = tempfile.mkdtemp(prefix="hyperlite-access-log-tests-")
# Nor /var/lib/hyperlite and /etc/hyperlite (app/core/paths.py): what the suite writes there (ISOs, templates, keys)
# goes to directories of its own.
os.environ["HYPERLITE_VAR_DIR"] = tempfile.mkdtemp(prefix="hyperlite-var-tests-")
os.environ["HYPERLITE_ETC_DIR"] = tempfile.mkdtemp(prefix="hyperlite-etc-tests-")
# No hyperlite-daemon runs here: sessions are signed in this process, with keys under HYPERLITE_ETC_DIR
# (app/core/tickets.py).
os.environ["HYPERLITE_AUTH_SIGNING"] = "local"
# Nor any operation hyperlite-daemon does as root: run in this process, through its registry (app/core/privileged.py).
os.environ["HYPERLITE_DAEMON_INPROCESS"] = "1"

import pytest


@pytest.fixture()
def database(tmp_path, monkeypatch):
    """A fresh, isolated SQLite database with the application schema."""
    from app.core import audit, cluster_lead, self_node
    from app.core import database as db_module
    from app.repositories.cfs import ids

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    self_node.forget()
    cluster_lead.forget()  # what a previous test's daemon said
    ids.forget()
    db_module.init_db()
    yield db_module
    audit._AUDIT_QUEUE.join()  # let the background audit writer finish before the database disappears


@pytest.fixture()
def make_user(database):
    """Insert a user directly in the database and return its username."""
    from app.core.security import hash_password

    def _make(username, role="admin", password="correct horse battery"):
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
                (username, hash_password(password), role),
            )
            conn.commit()
        return username

    return _make


@pytest.fixture()
def client(database):
    """HTTP client bound to the real FastAPI application and the isolated database."""
    from fastapi.testclient import TestClient

    from app.core import vm_limits
    from app.main import app

    vm_limits._cache.update(at=0.0, value=None)
    # The API under its prefix (app/main.py): a test names a route as the API documents it ("/vms"). What lives at the
    # root (/health, the SSO sign-in, the dashboard's pages) is reached with root_client.
    return TestClient(app, base_url="http://testserver/api/v1/")


@pytest.fixture()
def root_client(database):
    """HTTP client for the addresses outside the API: /health, the SSO sign-in and callback, downloads, the pages."""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def auth_headers(client, make_user):
    """Log a user in through the real /auth/login endpoint and return bearer headers."""

    def _login(username, role="admin", password="correct horse battery"):
        make_user(username, role, password)
        response = client.post("/auth/login", data={"username": username, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture()
def cluster(database, tmp_path, monkeypatch):
    """Registered remote nodes simulated by one directory each: ssh and scp are replaced by fakes that act on
    those directories, so ISO sharing is tested without any real host."""
    from app.core import disk_library, iso_share
    from app.routers import isos

    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(isos, "ISOS_DIR", local)
    monkeypatch.setattr(disk_library, "ISOS", local)  # the library hyperlite-daemon writes
    roots = {}

    def add_node(name, hostname, reachable=True, library=True, layout="old"):
        """layout: where the node keeps its library, "old" (/root/hyperlite, up to 1.0) or "new" (from 1.1)."""
        root = tmp_path / name
        root.mkdir()
        if library:
            (root / "isos").mkdir()
        roots[hostname] = {"root": root, "reachable": reachable, "library": library, "layout": layout, "commands": []}
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, status, added_at) VALUES (?, ?, 'root', 22, 'online', ?)",
                (name, hostname, datetime.now(UTC).isoformat()),
            )
            conn.commit()
        return root / "isos"

    def remote_path(host, path):
        # Every node keeps its library at the same absolute path as the local one.
        return roots[host]["root"] / "isos" / Path(path).name

    class Result:
        def __init__(self, code=0, out="", err=""):
            self.returncode, self.stdout, self.stderr = code, out, err

    def fake_ssh(node, command, timeout):
        host = roots[node["hostname"]]
        if not host["reachable"]:
            return Result(255, err="ssh: connect to host: Connection timed out")
        host["commands"].append(command)
        verb = command[0]
        if verb == "sh" and command[2] == iso_share._LIST:  # remote_isos: the library's place and content at once
            if not host["library"]:
                return Result(3)
            lines = [
                f"{p.name}\t{p.stat().st_size}\t{p.stat().st_mtime}"
                for p in sorted((host["root"] / "isos").glob("*.iso"))
            ]
            return Result(0, "\n".join(lines) + ("\n" if lines else ""))
        if verb == "sh":  # iso_share._library: where the node keeps its library
            if not host["library"]:
                return Result(1)
            return Result(0, iso_share.LIBRARY_DIRS[0 if host["layout"] == "new" else 1] + "\n")
        if verb == "find":
            if not host["library"]:
                return Result(1, err=f"find: '{command[1]}': No such file or directory")
            lines = [
                f"{p.name}\t{p.stat().st_size}\t{p.stat().st_mtime}"
                for p in sorted((host["root"] / "isos").glob("*.iso"))
            ]
            return Result(0, "\n".join(lines) + ("\n" if lines else ""))
        if verb == "stat":
            p = remote_path(node["hostname"], command[-1])
            return Result(0, f"{p.stat().st_size}\n") if p.exists() else Result(1, err="No such file")
        if verb == "mkdir":
            (host["root"] / "isos").mkdir(exist_ok=True)
            return Result()
        if verb == "mv":
            remote_path(node["hostname"], command[-2]).replace(remote_path(node["hostname"], command[-1]))
            return Result()
        if verb == "rm":
            remote_path(node["hostname"], command[-1]).unlink(missing_ok=True)
            return Result()
        raise AssertionError(f"unexpected remote command {command}")

    scp_calls = []

    def fake_scp(args, timeout=None):
        src, dst = args[-2], args[-1]
        scp_calls.append((src, dst))

        def resolve(spec):
            if "@" in spec and ":" in spec:
                host, path = spec.split("@", 1)[1].split(":", 1)
                if not roots[host]["reachable"]:
                    raise RuntimeError("Connection timed out")
                return remote_path(host, path)
            return Path(spec)

        shutil.copyfile(resolve(src), resolve(dst))

    monkeypatch.setattr(iso_share, "_ssh", fake_ssh)
    monkeypatch.setattr(iso_share, "_scp", fake_scp)
    monkeypatch.setattr(iso_share, "PROGRESS_EVERY_S", 0.01)
    return {"local": local, "add_node": add_node, "scp_calls": scp_calls, "roots": roots}


@pytest.fixture(autouse=True)
def _watchdog_stage(tmp_path, monkeypatch):
    """The updater's copy of its watchdog (app/routers/update.py) goes to the test's directory, never to the host's
    /var/lib/hyperlite."""
    from app.routers import update

    monkeypatch.setattr(update, "WATCHDOG_STAGE", tmp_path / "update-stage" / "update_watchdog.sh")


@pytest.fixture(autouse=True)
def _fresh_inventory():
    """The VM listing shared between requests (app/core/inventory_cache.py) never carries over from one test."""
    from app.core import guest_agent, inventory_cache

    inventory_cache.invalidate()
    guest_agent.forget()
    yield
    inventory_cache.invalidate()
    guest_agent.forget()


@pytest.fixture(autouse=True)
def _no_vm_claim_left():
    """The claims on VMs (app/core/vm_locks.py) never carry over from one test: a test that replaced the thread which
    releases one (a snapshot's, a backup's) left the VM busy for every later test of its worker, which then answered
    409 where it expected its own refusal."""
    from app.core import vm_locks

    yield
    with vm_locks._registry_lock:
        vm_locks._held.clear()


def agent_answer(addresses):
    """What qemu-guest-agent answers to guest-network-get-interfaces, from {interface: [(type, address)]}."""
    import json

    return json.dumps(
        {
            "return": [
                {"name": name, "ip-addresses": [{"ip-address-type": t, "ip-address": a} for t, a in addrs]}
                for name, addrs in addresses.items()
            ]
        }
    )


@pytest.fixture()
def fake_agent(monkeypatch):
    """The agent is asked through libvirt_qemu.qemuAgentCommand: a fake domain answers it with agent_reply()."""
    import types

    from app.core import guest_agent

    monkeypatch.setattr(
        guest_agent,
        "libvirt_qemu",
        types.SimpleNamespace(qemuAgentCommand=lambda domain, command, timeout, flags: domain.agent_reply(command)),
    )


@pytest.fixture()
def daemon_libvirt(monkeypatch):
    """The libvirt hyperlite-daemon's operations open (libvirt_utils.local_conn), in this process in the tests: call it
    with the fake connection they get. The API's own connection (open_conn) is faked apart."""
    from app.core import libvirt_utils

    def use(conn, nodes=None):
        """`nodes`: {name: fake} of the nodes registered over SSH only, as the daemon opens them with the cluster's
        key (libvirt_utils.node_conn); any other node gets `conn` too."""
        monkeypatch.setattr(libvirt_utils, "local_conn", lambda: contextlib.nullcontext(conn))
        monkeypatch.setattr(libvirt_utils, "open_local", lambda: conn)
        monkeypatch.setattr(
            libvirt_utils,
            "node_conn",
            lambda node=None: contextlib.nullcontext((nodes or {}).get(node, conn) if node else conn),
        )
        return conn

    return use


@pytest.fixture()
def daemon_lxc(monkeypatch):
    """The LXC driver hyperlite-daemon's container operations open (libvirt_utils.local_lxc_conn), in this process
    in the tests: call it with the fake connection they get. The API's own (open_lxc_conn) is faked apart."""
    from app.core import libvirt_utils

    def use(conn):
        monkeypatch.setattr(libvirt_utils, "local_lxc_conn", lambda: contextlib.nullcontext(conn))
        return conn

    return use
