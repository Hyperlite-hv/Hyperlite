"""`?wait=false` on the long operations that held the request until the copy ended (VM creation, clone, template
deployment and creation): every check answers at once, then 202 with the task, which the work closes. A proxy cuts a
request held for a large copy long before it ends; a client such as the Terraform provider follows the task instead."""

import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.core import audit, tasks, vm_locks
from app.routers import templates
from app.routers.vms import clone, create
from tests.test_clone_safety import FakeConn
from tests.test_template_deploy import _Conn, _xml


def _task(database, task_id):
    """The task once its work ended (the work runs on a thread; a loaded CI runner can take seconds)."""
    for _ in range(1000):
        audit._AUDIT_QUEUE.join()
        with database.get_conn() as db:
            row = dict(db.execute("SELECT type, status, error FROM tasks WHERE id = ?", (task_id,)).fetchone())
        if row["status"] != "running":
            return row
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} still running")


def _count(database, type_):
    with database.get_conn() as db:
        return db.execute("SELECT COUNT(*) FROM tasks WHERE type = ?", (type_,)).fetchone()[0]


@pytest.fixture()
def cloning(database, tmp_path, monkeypatch):
    src = tmp_path / "old.img"
    src.write_bytes(b"raw disk")
    images = tmp_path / "images"
    images.mkdir()
    conn = FakeConn(src)
    monkeypatch.setattr(clone, "open_conn", lambda node=None: conn)
    monkeypatch.setattr(clone, "IMAGES_DIR", images)
    monkeypatch.setattr(clone, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(clone, "generate_mac", lambda conn: "52:54:00:00:00:02")
    monkeypatch.setattr(clone, "allocate_static_ip", lambda *a: None)

    def copy(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"QFI\xfb")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(clone.subprocess, "run", copy)
    return {"conn": conn, "images": images}


def test_a_clone_answers_its_task_at_once(cloning, client, auth_headers, database):
    r = client.post("/vms/old/clone?wait=false", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "running" and body["name"] == "copy"
    task = _task(database, body["task_id"])
    assert task["type"] == "clone_vm" and task["status"] == "succeeded", task
    assert len(cloning["conn"].defined) == 1
    # The source and the new name are free again once the work ended.
    assert vm_locks.running("old") is None and vm_locks.running("copy") is None


def test_a_clone_refused_by_its_checks_answers_the_refusal_and_makes_no_task(
    cloning, client, auth_headers, database, monkeypatch
):
    monkeypatch.setattr(clone.disk_space, "allocated_bytes", lambda path: 50 * 1024**3)
    monkeypatch.setattr(clone.disk_space.shutil, "disk_usage", lambda path: type("U", (), {"free": 20 * 1024**3})())
    r = client.post("/vms/old/clone?wait=false", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert r.status_code == 409 and "Not enough space" in r.json()["detail"]
    assert _count(database, "clone_vm") == 0
    assert vm_locks.running("old") is None and vm_locks.running("copy") is None


def test_a_clone_that_fails_after_the_answer_closes_its_task(cloning, client, auth_headers, database, monkeypatch):
    def full(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"half")
        raise subprocess.CalledProcessError(1, cmd, "", "No space left on device")

    monkeypatch.setattr(clone.subprocess, "run", full)
    r = client.post("/vms/old/clone?wait=false", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert r.status_code == 202, r.text
    assert _task(database, r.json()["task_id"])["status"] == "failed"
    assert list(cloning["images"].iterdir()) == []
    assert vm_locks.running("copy") is None


@pytest.fixture()
def deploying(database, tmp_path, monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(templates, "IMAGES_DIR", tmp_path)
    monkeypatch.setattr(templates, "open_conn", lambda *a: conn)
    monkeypatch.setattr(templates, "refresh_pools_for_paths", lambda *a: None)
    monkeypatch.setattr(templates.maintenance, "refuse_if_in_maintenance", lambda *a: None)
    monkeypatch.setattr(templates.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(
        templates.templates_store, "get_template", lambda n: {"xml": _xml(None), "disk_path": "/t/base.qcow2"}
    )
    return conn


def test_a_deployment_has_a_task_and_can_answer_it_at_once(deploying, client, auth_headers, database):
    admin = auth_headers("root")
    r = client.post("/templates/base/deploy", json={"new_name": "copy"}, headers=admin)
    assert r.status_code == 201, r.text
    assert _count(database, "deploy_template") == 1

    r = client.post("/templates/base/deploy?wait=false", json={"new_name": "copy2"}, headers=admin)
    assert r.status_code == 202, r.text
    task = _task(database, r.json()["task_id"])
    assert task["type"] == "deploy_template" and task["status"] == "succeeded", task
    assert r.json()["name"] == "copy2" and len(deploying.defined) == 2
    assert vm_locks.running("copy2") is None


def test_two_deployments_never_write_the_same_disk(deploying, client, auth_headers):
    with vm_locks.claim("copy", "a deployment"):
        r = client.post("/templates/base/deploy", json={"new_name": "copy"}, headers=auth_headers("root"))
    assert r.status_code == 409 and deploying.defined == []


def test_a_creation_is_checked_then_answers_its_task(client, auth_headers, database, monkeypatch):
    calls = []
    proceed = threading.Event()

    def fake(payload, user, pending=frozenset(), check_only=False, task_id=None):
        calls.append((check_only, task_id))
        if not check_only:
            # The name is held while the work runs: a second creation of it is refused.
            assert vm_locks.running("web") == "a creation"
            proceed.wait(30)
            audit.log_action(user["username"], "create_vm", payload.name, "success", task_id=task_id)

    monkeypatch.setattr(create, "_create_vm", fake)
    monkeypatch.setattr(create.maintenance, "refuse_if_in_maintenance", lambda *a: None)
    body = {"name": "web", "vcpu": 1, "memory_mb": 512, "disks": [{"size_gb": 1}], "username": "u", "password": "pass"}
    admin = auth_headers("root")
    r = client.post("/vms?wait=false", json=body, headers=admin)
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    assert client.post("/vms?wait=false", json=body, headers=admin).status_code == 409
    proceed.set()
    assert _task(database, task_id)["status"] == "succeeded"
    assert calls == [(True, None), (False, task_id)]
    assert vm_locks.running("web") is None


def test_a_template_made_at_once_answers_its_task(client, auth_headers, database, monkeypatch):
    calls = []

    def fake(name, payload, user, check_only=False, task_id=None):
        calls.append((check_only, task_id))
        if not check_only:
            audit.log_action(user["username"], "clone_to_template", name, "success", task_id=task_id)

    monkeypatch.setattr(templates, "_convert_to_template", fake)
    r = client.post("/templates/from-vm/web?wait=false", json={"keep_vm": True}, headers=auth_headers("root"))
    assert r.status_code == 202, r.text
    task = _task(database, r.json()["task_id"])
    assert task["type"] == "clone_to_template" and task["status"] == "succeeded"
    assert calls == [(True, None), (False, r.json()["task_id"])] and r.json()["name"] == "web"
    assert vm_locks.running("web") is None


def test_a_task_its_work_left_open_is_closed_with_the_error(database):
    task_id = tasks.create_task("clone_vm", "web", username="root")

    def work():
        raise HTTPException(status_code=500, detail="Source disk path not found")

    assert tasks.answer_later(task_id, work, name="copy").status_code == 202
    task = _task(database, task_id)
    assert task["status"] == "failed" and task["error"] == "Source disk path not found"


def test_a_task_ends_only_once_its_work_released_the_vm(database):
    """A client that sees the task end acts on the VM at once: it must not find it still busy."""
    task_id = tasks.create_task("clone_vm", "web", username="root")
    claim = vm_locks.claim("copy", "a clone")
    recorded, letting_go = threading.Event(), threading.Event()

    def work():
        with claim:
            audit.log_action("root", "clone_vm", "web", "success", task_id=task_id)
            recorded.set()
            letting_go.wait(5)

    tasks.answer_later(task_id, work, name="copy")
    assert recorded.wait(5)
    assert tasks.task_status(task_id) == "running"  # the work still holds the VM
    letting_go.set()
    assert _task(database, task_id)["status"] == "succeeded"
    assert vm_locks.running("copy") is None
