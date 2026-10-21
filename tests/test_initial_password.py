"""The admin account's random initial password (code review, #448): changed at the first sign-in, and every copy of
it (a root-only file, the service's environment file, the copy the service writes in /etc/hyperlite/priv) removed once the admin account has a
password of its own. The console banner (scripts/write-motd.sh) shows it only while that file exists."""

import threading
import time

import pytest

from app.core import initial_password, seed
from app.core.security import hash_password, set_password
from app.repositories import registry


@pytest.fixture()
def copies(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERLITE_INITIAL_PASSWORD_ROOT", str(tmp_path))
    files, env = initial_password._paths()
    for path in [*files, env]:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in files:
        path.write_text("Initial-Pass-123\n")
    env.write_text("HYPERLITE_SECRET_KEY=abc\nHYPERLITE_INITIAL_ADMIN_PASSWORD=Initial-Pass-123\nOTHER=1\n")
    env.chmod(0o600)
    return files, env


def test_the_random_initial_password_is_changed_at_the_first_sign_in(database, monkeypatch):
    monkeypatch.setenv("HYPERLITE_INITIAL_ADMIN_PASSWORD", "Initial-Pass-123")
    assert seed.seed_admin() == "Initial-Pass-123"
    assert registry.accounts().sync.get("admin")["must_change_password"] == 1


def test_a_password_the_administrator_typed_at_installation_is_theirs(database, monkeypatch):
    monkeypatch.setenv("HYPERLITE_INITIAL_ADMIN_PASSWORD", "Their-Own-Pass-123")
    seed.seed_admin(must_change=False)
    assert registry.accounts().sync.get("admin")["must_change_password"] == 0


def test_changing_the_admin_password_removes_every_copy_of_the_initial_one(database, copies):
    files, env = copies
    seed.seed_admin()
    set_password("admin", "A-new-long-password-42")
    assert not any(path.exists() for path in files)
    assert env.read_text() == "HYPERLITE_SECRET_KEY=abc\nOTHER=1\n"
    assert env.stat().st_mode & 0o777 == 0o600


def test_another_account_s_password_leaves_them(database, copies, make_user):
    files, env = copies
    make_user("bob")
    set_password("bob", "A-new-long-password-42")
    assert all(path.exists() for path in files) and "INITIAL_ADMIN_PASSWORD" in env.read_text()


def test_a_copy_that_cannot_be_removed_never_fails_the_password_change(database, copies, monkeypatch):
    _files, env = copies
    seed.seed_admin()

    def refuse(*a, **k):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(initial_password.os, "replace", refuse)
    set_password("admin", "A-new-long-password-42")
    assert "INITIAL_ADMIN_PASSWORD" in env.read_text()  # left, with a warning in the journal
    assert not env.with_name(env.name + ".tmp").exists()


def test_the_suite_never_points_at_the_hosts_files():
    files, env = initial_password._paths()
    assert not str(files[0]).startswith("/root/") and not str(env).startswith("/etc/")


def test_an_initial_password_already_changed_is_removed_at_start(database, copies):
    """Nodes installed before this fix kept the file after the password was changed, and their console banner
    showed it."""
    files, env = copies
    seed.seed_admin()
    registry.accounts().sync.update("admin", hashed_password=hash_password("Changed-1"))
    initial_password.settle()
    assert not any(path.exists() for path in files) and "INITIAL_ADMIN_PASSWORD" not in env.read_text()


def test_an_initial_password_still_in_use_is_changed_at_the_next_sign_in(database, copies, monkeypatch):
    files, _env = copies
    monkeypatch.setenv("HYPERLITE_INITIAL_ADMIN_PASSWORD", "Initial-Pass-123")
    seed.seed_admin(must_change=False)  # as installations before this fix made it
    initial_password.settle()
    assert registry.accounts().sync.get("admin")["must_change_password"] == 1
    assert all(path.exists() for path in files)  # still the way in, until it is changed


def test_a_node_that_cannot_write_at_start_still_starts(database, copies, monkeypatch):
    """A cluster node starting without the quorum: the flag cannot be written, the service starts all the same."""
    monkeypatch.setenv("HYPERLITE_INITIAL_ADMIN_PASSWORD", "Initial-Pass-123")
    seed.seed_admin(must_change=False)

    def read_only(username):
        raise RuntimeError("This node has lost the cluster's quorum")

    monkeypatch.setattr(registry.accounts().sync, "flag_must_change", read_only)
    initial_password.settle()  # no exception
    assert registry.accounts().sync.get("admin")["must_change_password"] == 0  # the write was really refused


def test_a_node_whose_copy_went_stale_elsewhere_removes_it_at_its_next_check(database, copies, monkeypatch):
    """In a cluster the password is changed on one node: another node's own copy goes at its next check."""
    files, env = copies
    monkeypatch.setenv("HYPERLITE_INITIAL_ADMIN_PASSWORD", "Initial-Pass-123")
    seed.seed_admin()
    registry.accounts().sync.update("admin", hashed_password=hash_password("Changed-on-another-node-1"))
    assert all(path.exists() for path in files)  # the change came through the cluster: nothing ran here
    monkeypatch.setattr(initial_password, "CHECK_S", 0.01)
    monkeypatch.setattr(initial_password, "_stop", threading.Event())
    initial_password.start()
    deadline = time.time() + 5
    while any(path.exists() for path in files) and time.time() < deadline:
        time.sleep(0.02)
    initial_password._stop.set()
    assert not any(path.exists() for path in files) and "INITIAL_ADMIN_PASSWORD" not in env.read_text()
