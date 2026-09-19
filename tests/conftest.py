"""Shared pytest fixtures.

Secrets are read at import time by the application, so they are set here
before any `app.*` module is imported. Nothing in the test suite may write
to the repository (in particular no `.env`, no `hyperlite.db`).
"""

import os

from cryptography.fernet import Fernet

os.environ.setdefault("HYPERLITE_SECRET_KEY", "test-only-secret-key-0123456789abcdef0123456789abcdef")
os.environ.setdefault("HYPERLITE_ENCRYPTION_KEY", Fernet.generate_key().decode())

import pytest


@pytest.fixture()
def database(tmp_path, monkeypatch):
    """A fresh, isolated SQLite database with the application schema."""
    from app.core import audit
    from app.core import database as db_module

    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
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
    from app.routers import auth

    auth._login_failures.clear()
    auth._login_failures_by_ip.clear()
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
