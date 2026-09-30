"""The task and audit repositories (control plane v2, lot 2), run against every backend (SQLite today)."""

import asyncio

import pytest


@pytest.fixture(params=["sqlite"])
def repos(request, database):
    from app.repositories.sqlite.audit import SqliteAuditRepository
    from app.repositories.sqlite.tasks import SqliteTaskRepository

    return SqliteTaskRepository(), SqliteAuditRepository()


def run(coro):
    return asyncio.run(coro)


def test_a_task_lifecycle(repos):
    tasks, _audit = repos
    tasks.sync.create("t1", "create_vm", "web", None, "alice", "2026-09-30T10:00:00")
    tasks.sync.update_progress("t1", 40)
    assert tasks.sync.status("t1") == "en_cours" and run(tasks.get("t1"))["progres"] == 40
    tasks.sync.append_log("t1", "disk copied", "2026-09-30T10:00:05")
    tasks.sync.finish("t1", "echec", "no space", None, "2026-09-30T10:01:00")
    task = run(tasks.get("t1"))
    assert (task["statut"], task["erreur"], task["progres"]) == ("echec", "no space", 100)
    assert [line["message"] for line in run(tasks.logs("t1"))] == [
        "Started by alice",
        "disk copied",
        "Failed: no space",
    ]


def test_filters_sort_and_export(repos):
    tasks, _audit = repos
    for i, (type_, target) in enumerate([("create_vm", "web"), ("backup_container", "db"), ("create_vm", "web-2")]):
        tasks.sync.create(f"t{i}", type_, target, None, "alice", f"2026-09-30T10:0{i}:00")
    assert [t["id"] for t in run(tasks.list({"cible": "web"}))] == ["t2", "t0"]
    assert [t["id"] for t in run(tasks.list({"objet": "web"}))] == ["t0"]
    assert [t["id"] for t in run(tasks.list({"famille": "container"}))] == ["t1"]
    assert [t["id"] for t in run(tasks.list({}, sort="cree_le", order="asc", limit=2))] == ["t0", "t1"]
    assert run(tasks.list({}, sort="1; DROP TABLE tasks"))[0]["id"] == "t2"  # unknown sort: the default
    assert [row[0] for row in tasks.export_rows({"type": "create_vm"})] == ["t2", "t0"]


def test_interrupted_tasks_are_closed_and_stats_count_them(repos):
    tasks, _audit = repos
    tasks.sync.create("t1", "migrate_vm", "web", None, None, "2026-09-30T10:00:00")
    assert tasks.sync.close_interrupted("2026-09-30T11:00:00") == 1
    assert run(tasks.get("t1"))["statut"] == "echec"
    assert run(tasks.stats()) == {"running": 0, "total": 1, "failed": 1, "avg_duration_s": 0}
    assert tasks.sync.latest_running("migrate_vm", "2026-09-30T00:00:00") is None


def test_audit_query_count_and_purge(repos):
    _tasks, audit = repos
    audit.sync.append("2026-01-01T00:00:00", "alice", "delete_vm", "web", "succes", None, "10.0.0.1")
    audit.sync.append("2026-09-30T00:00:00", "bob", "start_vm", "web-2", "echec", "busy", None)
    assert [e["username"] for e in run(audit.query({}, 10))] == ["bob", "alice"]
    assert run(audit.count({"resource": "web"})) == 2 and run(audit.count({"result": "echec"})) == 1
    assert run(audit.actions()) == ["delete_vm", "start_vm"]
    assert [e["action"] for e in run(audit.for_resource("web"))] == ["delete_vm"]
    assert audit.sync.purge_before("2026-06-01T00:00:00") == 1
    assert [row[1] for row in audit.export_rows({})] == ["bob"]
