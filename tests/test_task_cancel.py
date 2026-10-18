"""Task log and cancellation: a stoppable task stops, one that cannot stop says so, one nobody runs is closed."""

import threading
import time

import pytest

from app.core import tasks


def _row(database, task_id):
    with database.get_conn() as conn:
        return dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())


def test_a_task_logs_its_start_and_end(database):
    task_id = tasks.create_task("backup_vm", "web", username="ana")
    tasks.task_log(task_id, "Copying vda")
    tasks.finish_task(task_id, "succeeded")
    assert [line["message"] for line in tasks.get_task_log(task_id)] == ["Started by ana", "Copying vda", "Finished"]
    failed = tasks.create_task("backup_vm", "web")
    tasks.finish_task(failed, "failed", "disk full")
    assert tasks.get_task_log(failed)[-1]["message"] == "Failed: disk full"


def test_a_stoppable_task_is_asked_to_stop_and_ends_as_cancelled(database):
    task_id = tasks.create_task("move_disk", "web", username="ana")
    aborted = []
    tasks.register_cancel(task_id, lambda: aborted.append(True))
    assert tasks.request_cancel(task_id, "bob") == "requested"
    assert aborted == [True]
    with pytest.raises(tasks.TaskCancelled):
        tasks.raise_if_cancelled(task_id)
    tasks.finish_task(task_id, "failed", "The move was cancelled")
    row = _row(database, task_id)
    assert row["status"] == "failed" and row["cancelled_by"] == "bob"
    assert row["error"] == "Cancelled by bob: The move was cancelled"
    assert not tasks.cancellable(task_id)
    with pytest.raises(ValueError):
        tasks.request_cancel(task_id, "bob")  # already over


def test_a_task_that_finishes_despite_the_request_stays_successful(database):
    task_id = tasks.create_task("migrate_vm", "web")
    tasks.register_cancel(task_id)
    tasks.request_cancel(task_id, "bob")
    tasks.finish_task(task_id, "succeeded")
    assert _row(database, task_id)["status"] == "succeeded" and _row(database, task_id)["cancelled_by"] is None


def test_a_running_task_without_a_clean_stop_is_refused_unless_forced(database):
    task_id = tasks.create_task("restore_backup", "web")
    with pytest.raises(tasks.NotStoppable):
        tasks.request_cancel(task_id, "bob")
    assert _row(database, task_id)["status"] == "running"
    assert tasks.request_cancel(task_id, "bob", force=True) == "abandoned"
    row = _row(database, task_id)
    assert row["status"] == "failed" and "may still be running" in row["error"]


def test_a_task_nobody_runs_is_closed_and_restarts_close_the_leftovers(database):
    with database.get_conn() as conn:
        for tid in ("orphan", "leftover"):
            conn.execute(
                "INSERT INTO tasks (id, type, target, status, created_at) VALUES (?, 'backup_vm', 'web', 'running', 'x')",
                (tid,),
            )
        conn.commit()
    assert tasks.request_cancel("orphan", "bob") == "abandoned"
    assert "no process was running it" in _row(database, "orphan")["error"]
    assert tasks.close_interrupted_tasks() == 1
    assert _row(database, "leftover")["error"].startswith("Interrupted: the Hyperlite service restarted")


def test_the_api_lists_logs_and_cancels_with_the_right_rules(client, auth_headers, database):
    admin = auth_headers("admin")
    viewer = auth_headers("viewer", role="viewer")
    task_id = tasks.create_task("move_disk", "web", username="admin")
    tasks.register_cancel(task_id)
    listed = next(t for t in client.get("/tasks", headers=admin).json() if t["id"] == task_id)
    assert listed["cancellable"] and listed["clean_shutdown"] and not listed["orphaned"]
    assert client.get(f"/tasks/{task_id}/log", headers=viewer).json()[0]["message"] == "Started by admin"
    assert client.get("/tasks/nope/log", headers=admin).status_code == 404
    assert client.post(f"/tasks/{task_id}/cancel", headers=viewer).status_code == 403  # not theirs
    assert client.post(f"/tasks/{task_id}/cancel", headers=admin).json() == {"result": "requested"}
    tasks.finish_task(task_id, "failed", "stopped")
    assert client.post(f"/tasks/{task_id}/cancel", headers=admin).status_code == 409

    stuck = tasks.create_task("restore_backup", "web", username="viewer")
    assert client.post(f"/tasks/{stuck}/cancel", headers=viewer).status_code == 409  # cannot stop midway
    assert client.post(f"/tasks/{stuck}/cancel?force=true", headers=viewer).status_code == 403  # admin only
    assert client.post(f"/tasks/{stuck}/cancel?force=true", headers=admin).json() == {"result": "abandoned"}


def test_cancelling_a_job_run_stops_the_command_in_progress(database):
    from app.core import jobs

    with database.get_conn() as conn:
        cur = conn.execute("INSERT INTO jobs (name, created_by, created_at) VALUES ('slow', 'x', 'now')")
        conn.execute(
            "INSERT INTO job_steps (job_id, position, target_type, command, condition_type, condition_value) "
            "VALUES (?, 0, 'host', 'sleep 30', 'exit_code', '0')",
            (cur.lastrowid,),
        )
        conn.commit()
        job_id = cur.lastrowid
    run_id = jobs.start_job_run(job_id, username="ana")
    with database.get_conn() as conn:
        task_id = conn.execute("SELECT task_id FROM job_runs WHERE id = ?", (run_id,)).fetchone()["task_id"]
    deadline = time.monotonic() + 5
    while "Step 1/1" not in " ".join(line["message"] for line in tasks.get_task_log(task_id)):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    time.sleep(0.2)  # the command is running
    started = time.monotonic()
    assert tasks.request_cancel(task_id, "bob") == "requested"
    while _row(database, task_id)["status"] == "running":
        assert time.monotonic() - started < 5, "the run did not stop"
        time.sleep(0.05)
    assert _row(database, task_id)["error"].startswith("Cancelled by bob")


def test_cancelling_a_live_disk_move_aborts_the_mirror(tmp_path):
    from app.core import disk_move

    class Domain:
        def __init__(self):
            self.aborted = []

        def blockCopy(self, *_a):
            pass

        def blockJobInfo(self, *_a):
            return {"cur": 1, "end": 10}

        def blockJobAbort(self, dev, flags):
            self.aborted.append(flags)

    stop = threading.Event()
    stop.set()
    domain = Domain()
    with pytest.raises(disk_move.MoveError) as err:
        disk_move._mirror_live(domain, "vda", str(tmp_path / "x.qcow2"), lambda pct: None, stop)
    assert err.value.message == disk_move.CANCELLED
    assert domain.aborted == [0]  # the mirror is cancelled, never pivoted
