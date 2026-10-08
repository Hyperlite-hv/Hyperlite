"""Shared pytest fixtures.

Secrets are read at import time by the application, so they are set here
before any `app.*` module is imported. Nothing in the test suite may write
to the repository (in particular no `.env`, no `hyperlite.db`).
"""

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet

os.environ.setdefault("HYPERLITE_SECRET_KEY", "test-only-secret-key-0123456789abcdef0123456789abcdef")
os.environ.setdefault("HYPERLITE_ENCRYPTION_KEY", Fernet.generate_key().decode())
# The name shared tables store for this node (app/core/self_node.py): fixed, so rows and cfs paths do not depend on the
# host the suite runs on. Tests of the name itself remove it.
os.environ.setdefault("HYPERLITE_NODE_NAME", "hv-test")

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
    from app.core import iso_share
    from app.routers import isos

    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(isos, "ISOS_DIR", local)
    roots = {}

    def add_node(name, hostname, reachable=True, library=True):
        root = tmp_path / name
        root.mkdir()
        if library:
            (root / "isos").mkdir()
        roots[hostname] = {"root": root, "reachable": reachable, "library": library}
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, statut, added_at) VALUES (?, ?, 'root', 22, 'en_ligne', ?)",
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
        verb = command[0]
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
    return {"local": local, "add_node": add_node, "scp_calls": scp_calls}


@pytest.fixture(autouse=True)
def _fresh_inventory():
    """The VM listing shared between requests (app/core/inventory_cache.py) never carries over from one test."""
    from app.core import inventory_cache

    inventory_cache.invalidate()
    yield
    inventory_cache.invalidate()
