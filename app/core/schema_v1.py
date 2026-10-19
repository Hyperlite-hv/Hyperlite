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
    "app_settings": {"cle": "key", "valeur": "value"},
    "backup_group_jobs": {
        "nom": "name",
        "valeur": "value",
        "exclues": "excluded",
        "frequence": "frequency",
        "heure": "time",
        "cible_dir": "target_dir",
        "garder_jours": "keep_days",
        "garder_semaines": "keep_weeks",
        "garder_mois": "keep_months",
        "actif": "active",
        "derniere_execution": "last_run",
        "prochaine_execution": "next_run",
    },
    "backup_jobs": {
        "frequence": "frequency",
        "heure": "time",
        "cible_dir": "target_dir",
        "actif": "active",
        "derniere_execution": "last_run",
        "prochaine_execution": "next_run",
        "garder_jours": "keep_days",
        "garder_semaines": "keep_weeks",
        "garder_mois": "keep_months",
    },
    "backups": {
        "chemin": "path",
        "taille_octets": "size_bytes",
        "cree_le": "created_at",
        "statut": "status",
        "erreur": "error",
        "verifie_le": "verified_at",
        "groupe_id": "group_id",
        "importe_de": "imported_from",
    },
    "config_copies": {
        "copie_le": "copied_at",
        "statut": "status",
        "taille": "size",
        "empreinte": "fingerprint",
        "erreur": "error",
    },
    "container_backups": {
        "chemin": "path",
        "taille_octets": "size_bytes",
        "cree_le": "created_at",
        "statut": "status",
        "erreur": "error",
    },
    "ha_protected_vms": {
        "etat_ha": "ha_state",
        "derniere_action": "last_action",
        "derniere_action_le": "last_action_at",
    },
    "ha_settings": {"cle": "key", "valeur": "value"},
    "job_run_logs": {
        "step_ordre": "step_position",
        "cible": "target",
        "commande": "command",
        "reussi": "succeeded",
        "horodatage": "timestamp",
    },
    "job_runs": {"statut": "status", "resultat": "result"},
    "job_steps": {
        "ordre": "position",
        "cible_type": "target_type",
        "cible": "target",
        "commande": "command",
        "condition_valeur": "condition_value",
    },
    "k8s_clusters": {
        "nom": "name",
        "reseau": "network",
        "serveur": "server",
        "memoire_mo": "memory_mb",
        "disque_go": "disk_gb",
        "adresse": "address",
        "statut": "status",
        "erreur": "error",
        "jeton": "token",
        "cree_par": "created_by",
        "cree_le": "created_at",
    },
    "metric_servers": {
        "nom": "name",
        "hote": "host",
        "jeton": "token",
        "prefixe": "prefix",
        "actif": "active",
        "dernier_envoi": "last_sent_at",
        "derniere_erreur": "last_error",
    },
    "metrics_samples": {"cible": "target"},
    "node_fencing": {
        "methode": "method",
        "adresse": "address",
        "utilisateur": "user",
        "tls_non_verifie": "tls_unverified",
        "modifie_par": "modified_by",
        "modifie_le": "modified_at",
    },
    "node_live": {
        "joignable": "reachable",
        "version_hyperviseur": "hypervisor_version",
        "version_libvirt": "libvirt_version",
    },
    "nodes": {"statut": "status", "derniere_verification": "last_verification"},
    "replication_jobs": {
        "nom": "name",
        "valeur": "value",
        "exclues": "excluded",
        "cible_dir": "target_dir",
        "intervalle_minutes": "interval_minutes",
        "actif": "active",
        "derniere_execution": "last_run",
        "prochaine_execution": "next_run",
    },
    "replication_state": {
        "cible_dir": "target_dir",
        "chaine": "chain",
        "dernier_point": "last_point",
        "dernier_ok_le": "last_success_at",
        "mtime_disques": "disks_mtime",
        "statut": "status",
        "erreur": "error",
        "maj_le": "updated_at",
    },
    "schedule_state": {"prochaine_execution": "next_run"},
    "shared_pools": {
        "nom": "name",
        "tous_les_noeuds": "all_nodes",
        "noeuds": "nodes",
        "cree_par": "created_by",
        "cree_le": "created_at",
    },
    "tasks": {
        "cible": "target",
        "statut": "status",
        "progres": "progress",
        "cree_le": "created_at",
        "debut_le": "started_at",
        "fin_le": "finished_at",
        "erreur": "error",
        "annule_par": "cancelled_by",
    },
}

# {(table, column, as named after COLUMNS): {old value: new value}}: the stored values that are words (states,
# results, frequencies, choices).
_TASK = {"en_attente": "pending", "en_cours": "running", "termine": "succeeded", "echec": "failed"}
_FREQUENCY = {"quotidien": "daily", "hebdomadaire": "weekly", "mensuel": "monthly"}
_SELECTION = {"toutes": "all", "etiquette": "tag"}
VALUES = {
    ("users", "role"): {"observateur": "viewer"},
    ("audit_log", "result"): {"succes": "success", "echec": "failed"},
    ("tasks", "status"): _TASK,
    ("backups", "status"): _TASK,
    ("backups", "mode"): {"chaud": "live", "froid": "cold"},
    ("container_backups", "status"): _TASK,
    ("config_copies", "status"): {"echec": "failed"},
    ("replication_state", "status"): {"echec": "failed"},
    ("backup_jobs", "frequency"): _FREQUENCY,
    ("backup_group_jobs", "frequency"): _FREQUENCY,
    ("backup_group_jobs", "selection"): _SELECTION,
    ("replication_jobs", "selection"): _SELECTION,
    ("job_steps", "target_type"): {"chaque_cible": "each_target"},
    ("job_runs", "status"): {"en_cours": "running", "succes": "success", "echec": "failed"},
    ("nodes", "status"): {"en_ligne": "online", "hors_ligne": "offline", "inconnu": "unknown"},
    ("k8s_clusters", "status"): {"creation": "creating", "pret": "ready", "echec": "failed", "suppression": "deleting"},
}

# The cluster's configuration in the replicated tree (app/core/cluster_setup.py, /cluster/corosync.json), as a
# version before 1.0 wrote it: read with the new names (cluster_conf), rewritten once every member runs 1.0.
CLUSTER_CONF_MEMBERS = "membres"
CLUSTER_CONF_MEMBER = {"nom": "name", "adresse": "address", "voix": "votes"}


def cluster_conf(conf):
    """The cluster's configuration with the names of this version, whatever version wrote it."""
    if not isinstance(conf, dict) or CLUSTER_CONF_MEMBERS not in conf:
        return conf
    out = {k: v for k, v in conf.items() if k != CLUSTER_CONF_MEMBERS}
    out["members"] = [
        {CLUSTER_CONF_MEMBER.get(k, k): v for k, v in m.items()} if isinstance(m, dict) else m
        for m in conf[CLUSTER_CONF_MEMBERS]
    ]
    return out


# A copy of the configuration made before API v1 (app/core/config_copy.py): its metadata and key file.
CONFIG_COPY_META = {"controleur": "controller", "cree_le": "created_at", "empreinte": "fingerprint"}
CONFIG_COPY_KEYS = "cles.env"

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
