"""Confirmed writes in a cluster (docs/design/confirmed-writes.md): a request that changes the configuration answers
only once the cluster took its change, and a change the cluster refused is an error (409), never a success undone a
second later.

While a request runs (the middleware in app/main.py), the application's connections (ids.Connection) note the outbox
entries its own writes produce. A write transaction of the request takes SQLite's write lock before its first
statement (BEGIN IMMEDIATE), so no other connection adds to the outbox until it ends: every outbox id handed out
while it runs is this request's. Before the answer leaves, shadow.confirm() copies them and says
whether the cluster took them.

Outside a request (a task, a scheduler, the copy and apply loops) nothing is noted: those changes go out as before.
"""

import contextvars
import re
import sqlite3

_DML = re.compile(r"^\s*(?:INSERT|REPLACE|UPDATE|DELETE)\b", re.IGNORECASE)
_LOCKING_BEGIN = re.compile(r"^\s*BEGIN\s+(?:IMMEDIATE|EXCLUSIVE)\b", re.IGNORECASE)
_BEGIN = re.compile(r"^\s*BEGIN\b", re.IGNORECASE)


class Request:
    """The outbox entries one request's writes produced."""

    def __init__(self):
        self.ids = set()


_current = contextvars.ContextVar("cfs_confirm_request", default=None)


def begin():
    """Note the writes of the request running in this context from now on. Returns (request, token for end())."""
    request = Request()
    return request, _current.set(request)


def end(token):
    _current.reset(token)


def settle():
    """Have the cluster take the writes the running request made so far, now rather than before its answer: for a
    request that then does something after which the cluster could not take them (a member added takes the quorum away
    until it is up). Raises shadow.NotConfirmed or shadow.Changed. Outside a request, or a single node: nothing."""
    request = _current.get()
    if request is None or not request.ids:
        return
    from app.repositories.cfs import shadow

    shadow.confirm(request.ids)
    request.ids.clear()


def _top(conn):
    """The last outbox id handed out (AUTOINCREMENT's counter, not the largest id left: copied entries are deleted)."""
    row = sqlite3.Connection.execute(conn, "SELECT seq FROM sqlite_sequence WHERE name = 'cfs_outbox'").fetchone()
    return row[0] if row else 0


def before(conn, sql):
    """Run before each statement of an application connection."""
    if _current.get() is None or conn.isolation_level is None:  # autocommit: each statement is its own transaction
        return
    if not conn.in_transaction:
        conn.cfs_floor = None  # a transaction starts: what the last one noted is over
        if _DML.match(sql):
            sqlite3.Connection.execute(conn, "BEGIN IMMEDIATE")
            conn.cfs_floor = _top(conn)


def after(conn, sql):
    """Run after each statement of an application connection."""
    request = _current.get()
    if request is None or not conn.in_transaction:
        return
    if _BEGIN.match(sql):
        # A transaction the code began itself: its entries are known only if it holds the write lock from the start.
        conn.cfs_floor = _top(conn) if _LOCKING_BEGIN.match(sql) else None
        return
    floor = getattr(conn, "cfs_floor", None)
    if floor is not None and _DML.match(sql):
        request.ids.update(range(floor + 1, _top(conn) + 1))
