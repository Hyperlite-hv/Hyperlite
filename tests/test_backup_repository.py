"""The backup repository (control plane v2, lot 5): VM backups, schedules, grouped jobs and container backups,
run against every backend (SQLite today)."""

import asyncio

import pytest


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.backups import SqliteBackupRepository

    return SqliteBackupRepository()


def run(coro):
    return asyncio.run(coro)


def test_vm_backup_catalogue(repo):
    store = repo.sync
    first = store.create("web", None, "/b/web/1", "cold", "2026-09-01T00:00:00", "t1")
    second = store.create("web", None, "/b/web/2", "live", "2026-09-02T00:00:00", "t2")
    store.mark_done(first, 1024, "abc")
    store.mark_failed(second, "no space")
    assert [(b["id"], b["status"]) for b in run(repo.list_for_vm("web"))] == [(second, "failed"), (first, "succeeded")]
    assert [b["id"] for b in store.finished_of("web")] == [first]
    assert store.next_to_verify("2026-10-01T00:00:00") == first
    store.set_verification(first, "verified", "2026-09-30T00:00:00", None)
    assert store.next_to_verify("2026-09-15T00:00:00") is None
    store.set_group(first, 7)
    assert run(repo.get(first))["group_id"] == 7
    store.delete(second)
    assert [b["id"] for b in run(repo.list_all())] == [first]


def test_schedules(repo):
    store = repo.sync
    store.upsert_schedule("web", "daily", "02:00", "/b", 7, None, None, None, "2026-10-01T02:00:00")
    store.upsert_schedule("web", "weekly", "03:00", "/b", 4, 7, 4, 12, "2026-10-05T03:00:00")
    schedule = run(repo.get_schedule("web"))
    assert (schedule["frequency"], schedule["keep_months"], schedule["active"]) == ("weekly", 12, 1)
    assert [s["vm_name"] for s in store.due_schedules("2026-10-06T00:00:00")] == ["web"]
    store.record_schedule_run(schedule["id"], "2026-10-05T03:00:00", "2026-10-12T03:00:00")
    assert store.due_schedules("2026-10-06T00:00:00") == []
    store.delete_schedule("web")
    assert run(repo.list_schedules()) == []


def test_grouped_jobs(repo):
    store = repo.sync
    values = ["all", "all", None, ["db"], "daily", "01:00", "/b", 7, None, None, None, True, None]
    job_id = store.save_group_job(values, "2026-10-01T01:00:00")
    assert store.group_name_taken("all") and not store.group_name_taken("all", job_id)
    job = store.get_group_job(job_id)
    assert (job["excluded"], job["active"]) == ('["db"]', 1)
    store.save_group_job([*values[:11], False, None], "2026-10-02T01:00:00", job_id)
    assert store.get_group_job(job_id)["active"] == 0 and store.due_group_jobs("2026-12-01T00:00:00") == []
    assert store.delete_group_job(job_id) and not store.delete_group_job(job_id)


def test_container_backups(repo):
    store = repo.sync
    backup_id = store.create_container_backup("pg", "/b/pg.tar.gz", "2026-09-30T00:00:00", "t1")
    store.container_backup_done(backup_id, 2048)
    assert [(b["container_name"], b["size_bytes"]) for b in run(repo.list_container_backups())] == [("pg", 2048)]
    store.container_backup_failed(backup_id, "disk full")
    assert store.get_container_backup(backup_id)["status"] == "failed"
    store.delete_container_backup(backup_id)
    assert store.get_container_backup(backup_id) is None
