"""Integer ids that two nodes of a cluster never both hand out (docs/design/hyperlite-cfs.md, sections 4.3 and 8).

The replicated tables number their rows with SQLite's AUTOINCREMENT, and a row's id is part of its path in the tree
(/db/groups/7): two nodes creating a group at once must not both pick 7. SQLite gives a new row one more than the
largest of sqlite_sequence and the ids already in the table, so the connections of the application (Connection,
used by app/core/database.py) set sqlite_sequence right before each insert into such a table: one less than a fresh id
from the daemon's cluster-wide counter, taken while the connection holds SQLite's write lock. Every such id is larger
than any id handed out before it on any node, so the row gets exactly the id reserved for it. (A trigger cannot do it:
SQLite picks the id before triggers run, and writes sqlite_sequence back when the statement ends.)

The counter starts at 100; OFFSET_PATH lifts it above the ids the cluster's first node already had: written once, by
the first copy into a cluster (seed). Outside a cluster nothing is reserved and SQLite numbers rows as before. Rows that
come from another node carry their id: they reserve nothing (Connection.reserve_ids).

In a cluster whose daemon does not answer, a new row cannot get a safe id: the insert is refused, as any configuration
change without quorum.
"""

import contextlib
import logging
import re
import sqlite3
import threading

from app.core.cfs_client import MUST_NOT_EXIST, Conflict, NotFound
from app.repositories.cfs.tables import TABLES

logger = logging.getLogger(__name__)

OFFSET_PATH = "/cluster/id-offset"
ROUND = 1000

_INSERT = re.compile(r'^\s*(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO\s+["`\[]?(\w+)', re.IGNORECASE)

_lock = threading.Lock()
_offset = None
_numbered = None


class NoId(sqlite3.OperationalError):
    """In a cluster, no id could be reserved for a new row."""


def numbered_tables(db):
    """The replicated tables numbered by AUTOINCREMENT."""
    return [
        name
        for name, sql in db.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'").fetchall()
        if name in TABLES and re.search(r"\bAUTOINCREMENT\b", sql or "", re.IGNORECASE)
    ]


def _in_cluster():
    from app.core import cluster_lead

    # Never records anything: this runs inside the insert's own write transaction.
    return cluster_lead.mode(record=False) == "cluster"


def _client():
    from app.repositories.cfs import shadow

    return shadow._get_client()


def offset(client):
    global _offset
    with _lock:
        if _offset is not None:
            return _offset
    try:
        value = int(client.get(OFFSET_PATH).data)
    except NotFound:
        value = 0  # a cluster made before ids were reserved: its counter is all there is
    with _lock:
        _offset = value
    return value


def reserve():
    """A fresh cluster-wide id, or 0 outside a cluster."""
    if not _in_cluster():
        return 0
    client = _client()
    return client.next_id() + offset(client)


def set_offset(db, client):
    """Run by the first copy into a cluster: ids handed out from now on start above every id this node has."""
    global _offset
    top = 0
    for table in numbered_tables(db):
        # Names come from the schema.
        top = max(top, db.execute(f'SELECT COALESCE(MAX(id), 0) FROM "{table}"').fetchone()[0])  # noqa: S608
    with contextlib.suppress(Conflict):  # set by the cluster's first node: it holds
        client.put(OFFSET_PATH, str((top // ROUND + 1) * ROUND).encode(), MUST_NOT_EXIST)
    with _lock:
        _offset = None


def forget():
    global _offset, _numbered
    with _lock:
        _offset = _numbered = None


class Connection(sqlite3.Connection):
    """A connection that reserves the id of each new row of a replicated table in a cluster."""

    reserve_ids = True

    def _before(self, sql):
        global _numbered
        if not self.reserve_ids:
            return
        match = _INSERT.match(sql)
        if not match:
            return
        if _numbered is None:
            names = set(numbered_tables(self))
            with _lock:
                _numbered = names
        table = match.group(1)
        if table not in _numbered or not _in_cluster():
            return
        execute = super().execute
        # Take the write lock first: another connection of this node cannot insert between the reservation and this
        # insert, so no id reserved earlier can come after this one.
        execute(
            "INSERT INTO sqlite_sequence (name, seq) SELECT ?, 0 WHERE NOT EXISTS "
            "(SELECT 1 FROM sqlite_sequence WHERE name = ?)",
            (table, table),
        )
        try:
            fresh = reserve()
        except Exception as e:
            logger.warning("hyperlite-cfs: no id could be reserved for a new row of %s: %s", table, e)
            raise NoId(f"hyperlite-cfs does not answer: no new row of {table} can be created") from None
        execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (fresh - 1, table))

    def execute(self, sql, parameters=(), /):
        self._before(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters, /):
        if self.reserve_ids and _INSERT.match(sql):
            cursor = None
            for parameters in seq_of_parameters:  # one reservation per row
                cursor = self.execute(sql, parameters)
            return cursor if cursor is not None else super().executemany(sql, [])
        return super().executemany(sql, seq_of_parameters)

    def cursor(self, factory=None):
        return super().cursor(factory or Cursor)


class Cursor(sqlite3.Cursor):
    def execute(self, sql, parameters=(), /):
        self.connection._before(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters, /):
        if self.connection.reserve_ids and _INSERT.match(sql):
            for parameters in seq_of_parameters:
                self.execute(sql, parameters)
            return self
        return super().executemany(sql, seq_of_parameters)
