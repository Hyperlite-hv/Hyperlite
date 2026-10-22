"""Hyperlite's own update is started by hyperlite-daemon in a unit of its own (app/daemon/ops/hyperlite.py): the
package it installs restarts the daemon and the API, which the job must outlive. The daemon checks the request again."""

import subprocess

import pytest

from app.core import privileged
from app.daemon.ops import hyperlite as ops
from app.daemon.registry import DaemonError
from app.routers import update


def _start(**args):
    return privileged.run("hyperlite.start_update", username="root", task_id="t-1", **args)


def test_the_daemon_refuses_what_cannot_be_done_here(monkeypatch):
    monkeypatch.setattr(update, "_install_method", lambda: "apt")
    monkeypatch.setattr(update, "_previous", lambda: None)
    monkeypatch.setattr(ops, "_launch", lambda args: pytest.fail("nothing starts"))
    from app.core import offline_update

    monkeypatch.setattr(offline_update, "stored", lambda: None)
    for kind, reason in (
        ("rollback", "No previous version"),
        ("offline", "No valid update file"),
        ("git", "from the mirror"),
    ):
        with pytest.raises(DaemonError) as e:
            _start(kind=kind)
        assert e.value.kind == "refused" and reason in e.value.detail, kind
    with pytest.raises(DaemonError) as e:
        _start(kind="anything")
    assert e.value.kind == "invalid_arguments"
    with pytest.raises(DaemonError) as e:
        privileged.run("hyperlite.start_update", username="root", task_id="t; rm -rf /", kind="apt")
    assert e.value.kind == "invalid_arguments"


def test_on_a_node_the_job_runs_in_a_unit_of_its_own(monkeypatch):
    monkeypatch.setattr(update, "_install_method", lambda: "apt")
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    calls = []
    monkeypatch.setattr(
        ops.subprocess, "run", lambda argv, **k: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
    )
    ops.start_update(ops.StartArgs(kind="apt", task_id="abc-123", username="root"), None)
    [argv] = calls
    assert argv[:4] == ["systemd-run", "--quiet", "--collect", "--unit=hyperlite-update-job-abc-123"]
    assert argv[-5:] == ["app.daemon.update_job", "apt", "abc-123", "root", "1"]


def test_a_refused_start_ends_the_task_with_its_reason(client, auth_headers, monkeypatch, database):
    monkeypatch.setattr(update, "_install_method", lambda: "apt")
    monkeypatch.setattr(update, "_previous", lambda: {"version": "1.1.2", "file": "/x.deb"})
    monkeypatch.setattr(
        ops, "_launch", lambda args: (_ for _ in ()).throw(DaemonError("failed", "systemd-run refused"))
    )
    r = client.post("/update/rollback", headers=auth_headers("root"))
    assert r.status_code == 502 and "systemd-run refused" in r.json()["detail"]
    from app.repositories import registry

    assert registry.tasks().sync.latest_running("hyperlite_update", "2000-01-01") is None  # not left running
