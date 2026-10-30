"""The accounts' start of a node (app/core/seed.py, first_start; docs/design/privilege-separation.md, 4e): run by
hyperlite-daemon at its own start, as only it writes the accounts, and by the API only when it runs the daemon's
operations itself (the test suite, the end-to-end backend)."""

import importlib

import pytest

from app.core import initial_password, paths, privileged, seed
from app.repositories import registry

daemon_main = importlib.import_module("app.daemon.__main__")


@pytest.fixture()
def node_files(tmp_path, monkeypatch):
    """The node's env file as postinst writes it, and its root-only password file, under tmp_path."""
    monkeypatch.setenv("HYPERLITE_INITIAL_PASSWORD_ROOT", str(tmp_path))
    monkeypatch.delenv("HYPERLITE_INITIAL_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(paths, "INITIAL_PASSWORD_FILE", tmp_path / "priv" / "initial-admin-password.txt")
    env = initial_password._paths()[1]
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text("HYPERLITE_SECRET_KEY=abc\nHYPERLITE_INITIAL_ADMIN_PASSWORD='Initial-Pass-123'\n")
    return tmp_path


def test_the_daemon_finds_the_installations_password_in_the_env_file(database, node_files):
    # hyperlite-daemon's unit loads no EnvironmentFile: the password postinst wrote is read from the file itself.
    assert seed.seed_admin() == "Initial-Pass-123"


def test_the_daemon_makes_the_admin_account_at_its_start_without_its_own_socket(database, node_files, monkeypatch):
    def no_socket(op, **args):
        pytest.fail(f"{op} was asked of the daemon's socket, which does not answer yet")

    monkeypatch.setattr(privileged, "write", no_socket)
    monkeypatch.setattr(initial_password, "start", lambda: initial_password.settle())  # no loop left behind
    daemon_main._accounts_at_start()
    admin = registry.accounts().sync.get("admin")
    assert admin is not None and admin["must_change_password"] == 1
    assert paths.INITIAL_PASSWORD_FILE.read_text() == "Initial-Pass-123\n"
    assert paths.INITIAL_PASSWORD_FILE.stat().st_mode & 0o777 == 0o600
    daemon_main._accounts_at_start()  # a restart: nothing made twice
    assert registry.accounts().sync.get("admin")["id"] == admin["id"]


def test_a_failed_start_is_logged_and_the_daemon_starts_all_the_same(database, monkeypatch, caplog):
    def broken():
        raise RuntimeError("no database")

    monkeypatch.setattr(seed, "first_start", broken)
    daemon_main._accounts_at_start()
    assert "The accounts' start failed" in caplog.text


def test_the_api_does_it_only_with_the_daemons_operations_in_process(monkeypatch):
    monkeypatch.setenv(privileged.INPROCESS_ENV, "1")
    assert not seed.in_daemon()
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    assert seed.in_daemon()
