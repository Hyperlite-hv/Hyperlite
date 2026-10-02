"""Phase D of hyperlite-cfs (docs/design/hyperlite-cfs.md, section 8): the replicated tree is the source of truth.

In a cluster (the daemon in cluster mode), the changes other nodes made reach this node's SQLite: apply() reads the
tree whenever the daemon's version moved, and writes into SQLite what differs, deleting rows the tree no longer has.
SQLite stays each node's working copy, as pmxcfs keeps its own database on every node; the application keeps reading
and writing it, and shadow.py copies this node's own changes out.

Safeguards, since a wrong apply would erase configuration:
- this node's changes go out first: nothing is applied while the outbox still holds some;
- a daemon that holds an older version than the one last applied here (its database was reset or replaced), or an
  empty tree while this node has configuration, is not applied: the report says so, and "Copy the database again"
  (seed) is the way out once an administrator decided which side is right;
- the whole apply is one SQLite transaction, under the write lock, and the rows it writes leave no trace in the outbox.
In local mode there is no other writer, so nothing is applied.
"""

import base64
import json
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime

from app.core.cfs_client import CfsError
from app.repositories.cfs import shadow

logger = logging.getLogger(__name__)

APPLIED = "cfs_applied_version"  # in app_settings, a row that stays local (tables.py)
INTERVAL_S = 2.0


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.applied = 0  # rows written by the last applies
        self.last_at = None
        self.problem = None


state = _State()


def _decode(value):
    if isinstance(value, dict) and set(value) == {"$b64"}:
        return base64.b64decode(value["$b64"])
    return value


def _problem(text):
    with state.lock:
        changed = state.problem != text
        state.problem = text
    if changed and text:
        logger.warning("hyperlite-cfs: %s", text)


def _applied_version(db):
    row = db.execute("SELECT valeur FROM app_settings WHERE cle = ?", (APPLIED,)).fetchone()
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def _fill(table, row):
    now = datetime.now(UTC).isoformat()
    for column, value in table.fill.items():
        row.setdefault(column, now if value == "now" else value)
    return row


def _upsert(db, table, row):
    columns = list(row)
    keys = ", ".join(f'"{c}"' for c in table.pk)
    updates = ", ".join(f'"{c}" = excluded."{c}"' for c in columns if c not in table.pk)
    names = ", ".join(f'"{c}"' for c in columns)
    marks = ", ".join("?" for _ in columns)
    # Table and column names come from tables.py and the schema; values are bound.
    sql = f'INSERT INTO "{table.name}" ({names}) VALUES ({marks}) ON CONFLICT({keys}) '  # noqa: S608
    sql += f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
    db.execute(sql, [row[c] for c in columns])


def _delete(db, table, row):
    where = " AND ".join(f'"{c}" = ?' for c in table.pk)
    db.execute(f'DELETE FROM "{table.name}" WHERE {where}', [row[c] for c in table.pk])  # noqa: S608


def apply():
    """Bring the tree's changes into SQLite. Returns the number of rows written or deleted, or None when nothing was
    applied (not in a cluster, nothing new, or a safeguard held it back: then state.problem says why)."""
    if not shadow.enabled():
        return None
    client = shadow._get_client()
    status = client.status()
    if status.mode != "cluster":
        _problem(None)
        return None
    with shadow._db() as db:
        last = _applied_version(db)
    if status.version == last:
        return None
    if status.version < last:
        _problem(
            f"hyperlite-cfs holds version {status.version}, older than the {last} this node applied: its database was "
            "reset or replaced. Nothing is applied until an administrator copies the right database again."
        )
        return None
    tables = shadow.tables()
    trees = {name: shadow._read_tree(client, t.prefix) for name, t in tables.items()}
    if client.status().version != status.version:
        return None  # changed while it was read: the next round reads it again
    if not any(trees.values()) and any(t.all() for t in tables.values()):
        _problem(
            "hyperlite-cfs holds no configuration while this node has some: nothing is applied until an administrator "
            "copies the database into it."
        )
        return None

    written = 0
    with shadow._db() as db:
        db.execute("BEGIN IMMEDIATE")  # no other write to SQLite until this apply is done
        try:
            if db.execute("SELECT COUNT(*) FROM cfs_outbox").fetchone()[0]:
                db.rollback()
                return None  # this node's own changes go out first
            top = db.execute("SELECT COALESCE(MAX(id), 0) FROM cfs_outbox").fetchone()[0]
            changes = []
            for name, table in tables.items():
                local = table.all(db)
                remote = trees[name]
                # Deletions, then updates, then new rows: a name freed elsewhere (a group renamed, another created
                # under its old name) is free here too before a row takes it again.
                changes += [(0, table, json.loads(local[p])) for p in local.keys() - remote.keys()]
                changes += [
                    (1 if p in local else 2, table, {k: _decode(v) for k, v in json.loads(data).items()})
                    for p, data in remote.items()
                    if local.get(p) != data
                ]
            for order, table, row in sorted(changes, key=lambda c: c[0]):
                if order == 0:
                    _delete(db, table, row)
                else:
                    _upsert(db, table, _fill(table, row))
                written += 1
            db.execute(
                "INSERT INTO app_settings (cle, valeur) VALUES (?, ?) ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
                (APPLIED, str(status.version)),
            )
            db.execute("DELETE FROM cfs_outbox WHERE id > ?", (top,))  # what was just written came from the tree
            db.commit()
        except sqlite3.IntegrityError as e:
            db.rollback()
            logger.warning("hyperlite-cfs: a change of %s refused by SQLite: %s", table.name, e)
            _problem(
                f"A change from another node to {table.name} breaks a rule of this node's database: nothing was "
                "applied. Copy the database again from the node that holds the right configuration."
            )
            return None
        except Exception:
            db.rollback()
            raise
    with state.lock:
        state.applied += written
        state.last_at = datetime.now(UTC).isoformat()
    _problem(None)
    return written


def mark_applied():
    """After a seed, the tree is what SQLite holds: record its version so the next apply does not undo anything."""
    try:
        version = shadow._get_client().status().version
    except (CfsError, OSError) as e:
        logger.warning("hyperlite-cfs: version after the copy unknown: %s", e)
        return
    with shadow._db() as db:
        db.execute(
            "INSERT INTO app_settings (cle, valeur) VALUES (?, ?) ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (APPLIED, str(version)),
        )
        db.execute("DELETE FROM cfs_outbox WHERE tbl = 'app_settings'")
        db.commit()
    _problem(None)


def report():
    with state.lock:
        return {"appliques": state.applied, "derniere_application": state.last_at, "probleme": state.problem}


def _loop():
    while True:
        time.sleep(INTERVAL_S)
        try:
            apply()
        except (CfsError, OSError) as e:
            logger.debug("hyperlite-cfs apply skipped: %s", e)  # the copy loop reports an unreachable daemon
        except Exception:  # the thread must survive anything a database or daemon can throw at it
            logger.exception("hyperlite-cfs: applying the tree failed; retrying")
            time.sleep(30)


def start_apply_loop():
    threading.Thread(target=_loop, daemon=True, name="cfs-apply").start()
