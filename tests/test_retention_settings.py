"""How long the users' addresses are kept (#448, finding F4): Hyperlite's own access log, in daily files kept the days
the administrator chose (30 by default, 0 for none), never the service journal; and the audit log's retention, set
from the same place (Activity > Audit log > Retention)."""

import logging
import os
import time

import pytest

from app.core import access_log, audit


@pytest.fixture()
def logs(database, tmp_path, monkeypatch):
    folder = tmp_path / "log"
    monkeypatch.setenv("HYPERLITE_ACCESS_LOG_DIR", str(folder))
    monkeypatch.setattr(access_log, "_handler", None)
    access = logging.getLogger("uvicorn.access")
    saved = (list(access.handlers), access.propagate, access.level)
    yield folder
    if access_log._handler is not None:
        access_log._handler.close()
    access.handlers[:] = saved[0]
    access.propagate = saved[1]
    access.setLevel(saved[2])


def _request(path="/vms?ticket=abc123", status=200):
    # What uvicorn logs for each request (uvicorn/protocols/http/*_impl.py).
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "192.168.1.20:51234", "GET", path, "1.1", status
    )


def test_requests_go_to_a_private_daily_file_never_the_journal(logs):
    access_log.apply()
    _request()
    access_log._handler.flush()
    line = (logs / "access.log").read_text()
    assert "192.168.1.20" in line and '"GET /vms HTTP/1.1" 200' in line
    assert "ticket" not in line  # one-time tickets of the query string are no business of a log
    assert (logs / "access.log").stat().st_mode & 0o777 == 0o640
    assert logging.getLogger("uvicorn.access").propagate is False  # nothing reaches the journal


def test_zero_days_keeps_no_access_log(logs):
    access_log.apply()
    _request()
    access_log.set_days(0)
    _request()
    assert not list(logs.glob("access.log*"))
    assert logging.getLogger("uvicorn.access").handlers == []


def test_files_older_than_the_retention_go(logs):
    logs.mkdir()
    old, recent = logs / "access.log.2026-09-01", logs / "access.log.2026-10-16"
    for path in (old, recent):
        path.write_text("x")
    week_ago = time.time() - 8 * 86400
    os.utime(old, (week_ago, week_ago))
    access_log.set_days(7)
    assert not old.exists() and recent.exists()


def test_the_retention_is_set_and_checked_through_the_api(logs, client, auth_headers, monkeypatch):
    monkeypatch.setenv("HYPERLITE_AUDIT_RETENTION_DAYS", "90")
    admin = auth_headers("root")
    r = client.get("/audit/retention", headers=admin)
    assert r.status_code == 200 and r.json()["access_log_days"] == 30 and r.json()["audit_days"] == 90
    r = client.put("/audit/retention", json={"access_log_days": 14, "audit_days": 180}, headers=admin)
    assert r.status_code == 200 and r.json()["access_log_days"] == 14 and r.json()["audit_days"] == 180
    assert audit.retention_days() == 180  # the setting wins over the environment's value
    assert client.put("/audit/retention", json={"access_log_days": -1}, headers=admin).status_code == 422
    assert client.put("/audit/retention", json={"audit_days": 99999}, headers=admin).status_code == 422
    observer = auth_headers("eve", role="viewer")
    assert client.put("/audit/retention", json={"audit_days": 1}, headers=observer).status_code == 403


def test_the_audit_purge_follows_the_setting(database):
    from datetime import UTC, datetime, timedelta

    with database.get_conn() as db:
        for days_ago in (5, 40):
            db.execute(
                "INSERT INTO audit_log (timestamp, username, action, resource, result) VALUES (?, 'bob', 'x', 'y', 'success')",
                ((datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),),
            )
        db.commit()
    audit.set_retention_days(30)
    assert audit.purge_old_entries() == 1


def test_a_missing_log_directory_never_stops_the_service(logs, monkeypatch):
    monkeypatch.setenv("HYPERLITE_ACCESS_LOG_DIR", "/proc/hyperlite-cannot-be-created")
    stop = access_log.start()
    stop.set()
    assert logging.getLogger("uvicorn.access").handlers == []
