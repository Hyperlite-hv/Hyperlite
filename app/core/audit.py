"""Audit log. See _AUDIT_QUEUE below for why writes are asynchronous."""

import contextvars
import logging
import os
import queue
import threading
import time
from datetime import UTC, datetime, timedelta

from app.core.database import get_conn
from app.core.tasks import _finish_task_in

# Design note: log_action() is called by almost every endpoint, even plain
# read-only GETs, so nearly every HTTP request also performs a SQLite write.
# WAL mode resolves reader-versus-writer contention but not writer-versus-writer
# contention (only one writer at a time, even in WAL), so "database is locked"
# can resurface under concurrent requests despite the 30 s timeout.
#
# Instead of writing to SQLite from every calling thread, a single dedicated
# thread owns audit-log writes: callers enqueue the entry (fast, non-blocking,
# no SQLite in the request thread) and return immediately, and the writer thread
# processes entries one at a time. That gives a single writer for the path that
# accounts for most of the application's write volume. Other tables are still
# written directly elsewhere, so this does not remove every possible concurrent
# write, only the most frequent source.
#
# The queue is bounded: in an extreme burst put_nowait() fails rather than
# blocking the calling request forever. The corresponding audit entry is then
# lost (logged to stderr), which is preferable to slowing the application down
# for a secondary log.
_AUDIT_QUEUE = queue.Queue(maxsize=10000)

logger = logging.getLogger(__name__)

# Successful READS are not audited: the dashboard polls the inventory every few seconds (four lists) and each
# console window its VM, which wrote some 2 500 rows an hour and buried the actions that matter. A failed read is
# still recorded (an unknown VM, a refused access: worth seeing).
READ_ACTION_PREFIXES = ("list_", "get_")

# How long the audit log keeps its entries (days); HYPERLITE_AUDIT_RETENTION_DAYS, 0 = forever.
DEFAULT_RETENTION_DAYS = 365
PURGE_INTERVAL_S = 86400
_last_purge = 0.0


def retention_days():
    raw = (os.environ.get("HYPERLITE_AUDIT_RETENTION_DAYS") or "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_RETENTION_DAYS


def purge_old_entries(now=None):
    """Delete the entries older than the retention. Returns how many were deleted."""
    days = retention_days()
    if days <= 0:
        return 0
    limit = ((now or datetime.now(UTC)) - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM audit_log WHERE timestamp < ?", (limit,))
        conn.commit()
    return cur.rowcount


def _purge_if_due():
    global _last_purge
    if time.monotonic() - _last_purge < PURGE_INTERVAL_S and _last_purge:
        return
    _last_purge = time.monotonic()
    try:
        deleted = purge_old_entries()
        if deleted:
            logger.info("Audit log: %d entries older than %d days deleted", deleted, retention_days())
    except Exception:
        logger.exception("Audit log purge failed")


# Source address of the HTTP request being served, set by a middleware in app/main.py:
# log_action() is called from dozens of endpoints that do not receive the request.
# Background jobs run outside any request and record NULL.
request_ip = contextvars.ContextVar("request_ip", default=None)
_writer_started = False
_writer_lock = threading.Lock()


def _writer_loop():
    while True:
        username, action, resource, result, error_message, ts, ip = _AUDIT_QUEUE.get()
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO audit_log (timestamp, username, action, resource, result, error_message, ip) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (ts, username, action, resource, result, error_message, ip),
                )
                conn.commit()
        except Exception:
            logger.exception("Audit write failed, entry lost: %s %s %s", action, resource, result)
        finally:
            _AUDIT_QUEUE.task_done()
        # The single writer also trims the table, at most once a day: nothing else writes audit rows.
        _purge_if_due()


def _ensure_writer_started():
    global _writer_started
    if _writer_started:
        return
    with _writer_lock:
        if not _writer_started:
            threading.Thread(target=_writer_loop, daemon=True, name="audit-writer").start()
            _writer_started = True


def log_action(
    username: str, action: str, resource: str, result: str, error_message: str | None = None, task_id: str | None = None
):
    """task_id: when provided (see app.core.tasks.create_task), the matching task
    is closed too. This stays SYNCHRONOUS (unlike the audit write itself, see
    above) because callers read the task status right after: a task stuck in
    "en_cours" until a queue drains would be a real regression. A
    log_action(..., "succes") or (..., "echec") call already marks the end of
    the task for every current synchronous endpoint."""
    if task_id:
        with get_conn() as conn:
            _finish_task_in(conn, task_id, "termine" if result == "succes" else "echec", error_message)
            conn.commit()

    if result == "succes" and action.startswith(READ_ACTION_PREFIXES):
        return
    _ensure_writer_started()
    entry = (username, action, resource, result, error_message, datetime.now(UTC).isoformat(), request_ip.get())
    try:
        _AUDIT_QUEUE.put_nowait(entry)
    except queue.Full:
        # Who, what and when only: the free-text message may quote user input (a refused password rule, an
        # identity provider's answer) and has no place in the service log.
        logger.error("Audit queue full, entry lost: %s %s %s (%s)", username, action, resource, result)

    # Outbound notifications: a single entry point instead of calling notify() at
    # every log_action() call site. It runs in a separate thread rather than in the
    # calling request: notify() performs network I/O (webhook/SMTP, up to 10 s of
    # timeout per channel), and blocking the HTTP response while a remote webhook
    # answers (or times out) would be a robustness problem in itself, independent
    # of SQLite.
    from app.core.notifications import NOTIFY_EVENTS, notify

    if action in NOTIFY_EVENTS:
        title = f"{NOTIFY_EVENTS[action]} — {resource}"
        message = error_message or f"{action} on '{resource}': {result}"
        threading.Thread(target=notify, args=(action, title, message, result), daemon=True).start()
