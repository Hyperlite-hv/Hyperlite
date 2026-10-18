"""API v1, the database in English (docs/design/api-v1.md, "The database"; names from docs/design/api-v1/names.json).

At the start of a version that renames columns or stored values, each node migrates its own database before anything
else touches it (init_db calls migrate() first): a copy of the file is kept next to it (hyperlite.db.before-v1, once),
then every rename is made in one transaction. A database already migrated has none of the old names: migrate() then
does nothing, so a node that stopped in the middle (the transaction rolled back) simply runs it again.

The replicated configuration (hyperlite-cfs) may still hold rows written before the migration, with the old names,
until the tree is rewritten (docs/design/api-v1.md, "The cluster update"): row() reads them with the new names.
"""

import logging
import shutil
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# {table: {old column: new column}}. Each step of the work adds its tables (docs/design/api-v1.md, "Order of work").
COLUMNS = {
    "webauthn_credentials": {"nom": "name", "cree_le": "created_at", "utilise_le": "used_at"},
    "webauthn_challenges": {"but": "purpose", "expire_le": "expires_at"},
}

# {(table, new column): {old value: new value}}: the stored values that are words (states, results, frequencies).
VALUES = {}

BACKUP_SUFFIX = ".before-v1"


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def pending(conn):
    """[(table, old, new)] of the columns this database still has under their old names."""
    out = []
    for table, renames in COLUMNS.items():
        existing = _columns(conn, table)
        out += [(table, old, new) for old, new in renames.items() if old in existing and new not in existing]
    return out


def _old_values(conn):
    """[(table, column, old, new)] of the stored values still in their old form."""
    out = []
    for (table, column), renames in VALUES.items():
        if column not in _columns(conn, table):
            continue
        for old, new in renames.items():
            # Table and column names come from this module, values are bound.
            if conn.execute(f'SELECT 1 FROM "{table}" WHERE "{column}" = ? LIMIT 1', (old,)).fetchone():  # noqa: S608
                out.append((table, column, old, new))
    return out


def _keep_copy(conn, db_path):
    """The database as it was before the first rename, once (a later run finds it and keeps it)."""
    copy = Path(f"{db_path}{BACKUP_SUFFIX}")
    if copy.exists():
        return
    work = copy.with_name(copy.name + ".part")
    target = sqlite3.connect(work)
    try:
        conn.backup(target)  # consistent with the WAL, unlike a copy of the file
    finally:
        target.close()
    work.chmod(0o600)
    shutil.move(work, copy)
    logger.info("Database copied to %s before the API v1 renames", copy)


def migrate(conn, db_path):
    """Rename what this database still holds under its old names. Returns the number of columns and values renamed."""
    columns = pending(conn)
    values = _old_values(conn)
    if not columns and not values:
        return 0
    _keep_copy(conn, db_path)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, old, new in columns:
            # SQLite (3.25+) renames the column in the indexes and triggers that name it too.
            conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"')
        for table, column, old, new in values:
            conn.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?', (new, old))  # noqa: S608
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info("API v1: %d columns and %d stored values renamed", len(columns), len(values))
    return len(columns) + len(values)


def row(table, data):
    """A row of `table` read from the replicated configuration, with the names of this version (a row written by a node
    before the migration keeps its old names until the tree is rewritten)."""
    renames = COLUMNS.get(table)
    if renames:
        data = {renames.get(k, k): v for k, v in data.items()}
    for (t, column), values in VALUES.items():
        if t == table and data.get(column) in values:
            data[column] = values[data[column]]
    return data
