from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.core.tasks import create_task, finish_task
from app.routers import update


def test_no_update_in_progress_by_default(database):
    assert update._update_in_progress() is None


def test_a_running_update_blocks_a_second_one(database, monkeypatch):
    create_task("hyperlite_update", "hyperlite", username="alice")
    assert update._update_in_progress() == "alice"

    monkeypatch.setattr(update, "_start_update", lambda user: {"started": True})
    with pytest.raises(HTTPException) as refused:
        update.apply_update({"username": "bob"})
    assert refused.value.status_code == 409
    assert "alice" in refused.value.detail


def test_a_finished_update_does_not_block(database, monkeypatch):
    task_id = create_task("hyperlite_update", "hyperlite", username="alice")
    finish_task(task_id, "termine")
    monkeypatch.setattr(update, "_start_update", lambda user: {"started": True})
    assert update.apply_update({"username": "bob"}) == {"started": True}


def test_a_stale_task_left_running_by_a_crash_does_not_block_forever(database):
    task_id = create_task("hyperlite_update", "hyperlite", username="alice")
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with database.get_conn() as conn:
        conn.execute("UPDATE tasks SET cree_le = ? WHERE id = ?", (old, task_id))
        conn.commit()
    assert update._update_in_progress() is None
