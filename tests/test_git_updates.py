"""An update from git applies unsigned code as root (GHSA-rm3v-9jj9-7qmj): only on a development checkout whose
administrator asked for it, and no dependency's install script runs."""

import pytest
from fastapi import HTTPException

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.daemon.registry import Context, dispatch
from app.routers import update


@pytest.fixture()
def checkout(monkeypatch):
    monkeypatch.setattr(update, "_install_method", lambda: "git")
    monkeypatch.setattr(update, "_is_dirty", lambda: False)
    started = []
    monkeypatch.setattr(update, "_start_job", lambda user, kind, task_id, *a: started.append(kind))
    monkeypatch.setattr(update, "create_task", lambda *a, **k: "t1")
    monkeypatch.delenv(update.GIT_UPDATES_ENV, raising=False)
    return started


def test_a_checkout_does_not_update_from_git_unless_asked(checkout, monkeypatch):
    with pytest.raises(HTTPException) as err:
        update._start_update({"username": "admin"})
    assert err.value.status_code == 409 and update.GIT_UPDATES_ENV in err.value.detail and checkout == []
    monkeypatch.setenv(update.GIT_UPDATES_ENV, "1")
    update._start_update({"username": "admin"})
    assert checkout == ["git"]


def test_the_daemon_refuses_a_git_update_unless_asked(checkout, monkeypatch):
    answer = dispatch(
        {"op": "hyperlite.start_update", "args": {"kind": "git", "task_id": "t1", "username": "admin"}},
        Context(None, None, 0, 0),
    )
    assert answer["ok"] is False and update.GIT_UPDATES_ENV in answer["detail"]


def test_neither_a_checkout_nor_the_package_has_nothing_to_update_from(monkeypatch):
    monkeypatch.setattr(update, "REPO_DIR", update.Path("/nonexistent"))
    monkeypatch.setattr(update, "_run_c", lambda argv: type("R", (), {"returncode": 1, "stdout": ""})())
    monkeypatch.setenv(update.GIT_UPDATES_ENV, "1")
    assert update._install_method() == "unknown" and not update.git_updates_allowed()
