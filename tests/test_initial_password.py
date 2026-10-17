"""The admin account's random initial password (code review, #448): changed at the first sign-in, and every copy of
it (a root-only file, the service's .env, the copy the service writes in data/) removed once the admin account has a
password of its own. The console banner (scripts/write-motd.sh) shows it only while that file exists."""

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
    assert not env.with_name(".env.tmp").exists()


def test_the_suite_never_points_at_the_hosts_files():
    files, env = initial_password._paths()
    assert not str(files[0]).startswith("/root/") and not str(env).startswith(str(initial_password.APP_DIR))


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
