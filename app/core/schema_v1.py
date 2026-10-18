"""API v1, the database in English (docs/design/api-v1.md, "The database"; names from docs/design/api-v1/names.json).

At the start of a version that renames columns or stored values, each node migrates its own database before anything
else touches it (init_db calls migrate() first): a copy of the file is kept next to it (hyperlite.db.before-v1, once),
then every rename is made in one transaction. A database already migrated has none of the old names: migrate() then
does nothing, so a node that stopped in the middle (the transaction rolled back) simply runs it again.

The replicated configuration (hyperlite-cfs) may still hold rows written before the migration, with the old names,
until the tree is rewritten (docs/design/api-v1.md, "The cluster update"): row() reads them with the new names.
"""

import logging
import re
import shutil
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# {table: {old column: new column}}. Each step of the work adds its tables (docs/design/api-v1.md, "Order of work").
COLUMNS = {
    "webauthn_credentials": {"nom": "name", "cree_le": "created_at", "utilise_le": "used_at"},
    "webauthn_challenges": {"but": "purpose", "expire_le": "expires_at"},
}

# {(table, column, as named after COLUMNS): {old value: new value}}: the stored values that are words (states,
# results, frequencies, choices).
_TASK = {"en_attente": "pending", "en_cours": "running", "termine": "succeeded", "echec": "failed"}
_FREQUENCY = {"quotidien": "daily", "hebdomadaire": "weekly", "mensuel": "monthly"}
_SELECTION = {"toutes": "all", "etiquette": "tag"}
VALUES = {
    ("users", "role"): {"observateur": "viewer"},
    ("audit_log", "result"): {"succes": "success", "echec": "failed"},
    ("tasks", "statut"): _TASK,
    ("backups", "statut"): _TASK,
    ("backups", "mode"): {"chaud": "live", "froid": "cold"},
    ("container_backups", "statut"): _TASK,
    ("config_copies", "statut"): {"echec": "failed"},
    ("replication_state", "statut"): {"echec": "failed"},
    ("backup_jobs", "frequence"): _FREQUENCY,
    ("backup_group_jobs", "frequence"): _FREQUENCY,
    ("backup_group_jobs", "selection"): _SELECTION,
    ("replication_jobs", "selection"): _SELECTION,
    ("job_steps", "cible_type"): {"chaque_cible": "each_target"},
    ("job_runs", "statut"): {"en_cours": "running", "succes": "success", "echec": "failed"},
    ("nodes", "statut"): {"en_ligne": "online", "hors_ligne": "offline", "inconnu": "unknown"},
    ("k8s_clusters", "statut"): {"creation": "creating", "pret": "ready", "echec": "failed", "suppression": "deleting"},
}

# The mode of a backup in the manifests written before API v1 (app/core/backup_integrity.py reads both).
MANIFEST_MODES = {"chaud": "live", "froid": "cold"}

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


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _definition(conn, table):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return row[0] if row else None


def _value_renames(table):
    """{column: {old: new}} of the stored values of `table`."""
    return {column: renames for (t, column), renames in VALUES.items() if t == table}


def _to_rebuild(conn):
    """Tables whose definition names an old value (a CHECK, a DEFAULT): SQLite changes those only by rebuilding the
    table."""
    out = []
    for table in sorted({t for t, _ in VALUES}):
        sql = _definition(conn, table)
        olds = {old for renames in _value_renames(table).values() for old in renames}
        if sql and any(_literal(old) in sql for old in olds):
            out.append(table)
    return out


def _rebuild(conn, table):
    """The usual way to change a table SQLite cannot alter: a new table with the new definition, the rows copied with
    their values renamed, the old one dropped, the new one renamed; its indexes and triggers made again, and its
    AUTOINCREMENT counter kept (an id of a deleted row is never handed out again)."""
    renames = _value_renames(table)
    sql = _definition(conn, table)
    for values in renames.values():
        for old, new in values.items():
            sql = sql.replace(_literal(old), _literal(new))
    work = f"{table}__v1"
    sql = re.sub(r'^\s*CREATE\s+TABLE\s+("?)\w+\1', f'CREATE TABLE "{work}"', sql, count=1, flags=re.IGNORECASE)
    others = [
        r[0]
        for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('index', 'trigger') AND tbl_name = ? AND sql IS NOT NULL",
            (table,),
        )
    ]
    has_sequence = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone()
    seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)).fetchone() if has_sequence else None
    columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]

    def mapped(column):
        values = renames.get(column)
        if not values:
            return f'"{column}"'
        cases = " ".join(f"WHEN {_literal(old)} THEN {_literal(new)}" for old, new in values.items())
        return f'CASE "{column}" {cases} ELSE "{column}" END'

    names = ", ".join(f'"{c}"' for c in columns)
    conn.execute(sql)
    # Names come from the schema and this module, never from a request.
    conn.execute(f'INSERT INTO "{work}" ({names}) SELECT {", ".join(mapped(c) for c in columns)} FROM "{table}"')  # noqa: S608
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{work}" RENAME TO "{table}"')
    for statement in others:
        conn.execute(statement)
    if seq is not None:
        conn.execute("UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = ?", (seq[0], table))


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
    rebuilt = _to_rebuild(conn)
    values = _old_values(conn)
    if not columns and not rebuilt and not values:
        return 0
    _keep_copy(conn, db_path)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, old, new in columns:
            # SQLite (3.25+) renames the column in the indexes, triggers and CHECKs that name it too.
            conn.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"')
        for table in rebuilt:
            _rebuild(conn, table)
        for table, column, old, new in _old_values(conn):
            conn.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?', (new, old))  # noqa: S608
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(
        "API v1: %d columns renamed, %d tables rebuilt, %d stored values renamed",
        len(columns),
        len(rebuilt),
        len(values),
    )
    return len(columns) + len(rebuilt) + len(values)


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
