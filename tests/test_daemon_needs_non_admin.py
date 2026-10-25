"""Operations a route open to non-administrators reaches (the review of #750, run as a call graph from every route):
each needs what that route lets in, so the daemon's check (app/daemon/needs.py, 4d) refuses nobody the route allows,
and still refuses the others."""

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import permissions, tasks
from app.daemon.registry import NEEDS, OPERATIONS
from app.repositories import registry


def _refusal(op, username, **args):
    model = OPERATIONS[op][0]
    return NEEDS[op].refusal(registry.accounts().sync.get(username), model(**args))


@pytest.fixture()
def people(make_user, database):
    make_user("oli", "viewer")  # manages the VM web
    make_user("vic", "viewer")  # nothing on it
    permissions.create_acl("user", "oli", "manager", "vm", "web")
    return database


def test_a_vm_manager_backs_up_into_a_store_and_stops_their_own_backup(people):
    assert _refusal("store.backup", "oli", vm="web", store="main", task_id="t") is None
    assert "vm.snapshot" in _refusal("store.backup", "vic", vm="web", store="main", task_id="t")
    assert _refusal("store.check", "vic", name="main") is None  # whether it exists and answers
    mine = tasks.create_task("backup_vm", "web", username="oli")
    assert _refusal("backup.cancel", "oli", task_id=mine) is None
    assert "started this task" in _refusal("backup.cancel", "vic", task_id=mine)


def test_the_retention_after_a_managers_backup_deletes_that_vms_backups_only(people):
    with people.get_conn() as db:
        db.execute(
            "INSERT INTO backups (vm_name, path, mode, status, created_at) VALUES ('web', '/b/web/1', 'cold', 'succeeded', 'now')"
        )
        db.execute(
            "INSERT INTO backups (vm_name, path, mode, status, created_at) VALUES ('db', '/b/db/1', 'cold', 'succeeded', 'now')"
        )
        db.commit()
    assert _refusal("backup.delete", "oli", path="/b/web/1") is None
    assert "vm.snapshot" in _refusal("backup.delete", "oli", path="/b/db/1")
    assert _refusal("backup.delete", "oli", path="/b/unknown") is not None


def test_a_vm_created_from_another_nodes_image_copies_it_for_its_own_task(people):
    copy = tasks.create_task("copy_iso", "debian.iso", username="oli")
    args = {"name": "debian.iso", "source": "n2", "target": None, "task_id": copy, "total": 1}
    assert _refusal("iso.copy", "oli", **args) is None
    assert _refusal("iso.copy", "vic", **args) is not None


def test_a_cluster_wide_list_over_ssh_is_any_accounts(people):
    from app.core.rolling_update import API_URL

    request = {"url": f"{API_URL}/api/v1/tasks", "method": "GET", "body": None, "timeout": 10, "token": "t"}
    assert _refusal("node.peer_call", "vic", node="n2", request=request) is None
