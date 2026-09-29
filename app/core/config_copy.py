"""Copy of the cluster configuration to the other nodes, and promotion of a node (docs/design/cluster-config.md, option
(a), as decided).

Hyperlite's state lives in one SQLite file on the controller. If the controller host is lost, the VMs on the other
nodes keep running, but the users, permissions, nodes, HA settings, backup schedules and secrets are gone. So:

  - Every COPY_CHECK_S the controller computes a digest of the configuration tables. When it changed, or when the last
    copy is older than COPY_MAX_AGE_S, it builds a bundle and sends it to every online node:
      hyperlite.db  consistent snapshot through SQLite's online backup API, without the telemetry tables;
      cles.env      the two keys of .env without which the copy is useless (sessions, encrypted secrets);
      meta.json     the source controller, the date, the digest.
    The bundle goes over the cluster's SSH key (encrypted in transit) into REMOTE_DIR on the node, a root-only
    directory (0700, file 0600). A node already trusts the controller with root access; the copy adds no new party.
  - `python -m app.core.config_copy promote` runs **on the node to promote**, by hand. It refuses while the old
    controller still answers (two controllers would each act on the cluster), shows the age of the copy, asks for a
    confirmation, then installs the database and the keys, rewrites what pointed at the node itself as "local", and
    starts the service.
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from app.core import database

logger = logging.getLogger(__name__)

REMOTE_DIR = "/var/lib/hyperlite/config-copy"
COPY_CHECK_S = 60
COPY_MAX_AGE_S = 15 * 60
TELEMETRY_TABLES = ("metrics_samples", "node_live", "storage_samples")
# The configuration: what an administrator changes and must never lose. Operations (tasks, audit) and telemetry
# change constantly; they travel with the periodic copy but do not trigger one.
CONFIG_TABLES = (
    "users",
    "custom_roles",
    "acl",
    "groups",
    "group_members",
    "pools",
    "pool_members",
    "nodes",
    "node_maintenance",
    "ha_protected_vms",
    "ha_settings",
    "node_fencing",
    "backup_jobs",
    "jobs",
    "notification_channels",
    "sso_config",
    "network_firewall",
    "vm_auto_cleanup",
    "api_tokens",
    "webauthn_credentials",
    "job_steps",
    "vm_ssh_users",
    "vm_os_label",
    "container_ssh_users",
)
KEY_NAMES = ("HYPERLITE_SECRET_KEY", "HYPERLITE_ENCRYPTION_KEY")
SERVICE = "hyperlite"


class CopyError(Exception):
    """A copy that did not reach a node, with a message written for the administrator."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _now():
    return datetime.now(UTC)


def _env_path():
    from app.core.secrets_crypto import ENV_PATH

    return ENV_PATH


def _read_keys():
    keys = {}
    env = _env_path()
    if env.exists():
        for line in env.read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() in KEY_NAMES:
                keys[name.strip()] = value.strip().strip("\"'")
    for name in KEY_NAMES:
        keys.setdefault(name, os.environ.get(name, ""))
    return keys


def config_digest(db_path=None):
    """SHA-256 over the rows of the configuration tables that exist in this database."""
    conn = sqlite3.connect(db_path or database.DB_PATH, timeout=30)
    try:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        h = hashlib.sha256()
        for table in CONFIG_TABLES:
            if table not in existing:
                continue
            h.update(table.encode())
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid"):  # noqa: S608 (fixed names only)
                h.update(repr(tuple(row)).encode())
        return h.hexdigest()
    finally:
        conn.close()


def build_bundle(dest_dir):
    """Write a bundle (tar.gz) in dest_dir and return (path, meta)."""
    dest_dir = Path(dest_dir)
    work = Path(tempfile.mkdtemp(dir=dest_dir))
    try:
        snapshot = work / "hyperlite.db"
        src = sqlite3.connect(database.DB_PATH, timeout=30)
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)  # consistent even while the service writes
            existing = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for table in TELEMETRY_TABLES:
                if table in existing:
                    dst.execute(f"DELETE FROM {table}")  # noqa: S608 (fixed names only)
            dst.commit()
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.execute("VACUUM")
        finally:
            dst.close()
            src.close()
        meta = {
            "version": 1,
            "controleur": socket.gethostname(),
            "cree_le": _now().isoformat(),
            "empreinte": config_digest(snapshot),
        }
        (work / "meta.json").write_text(json.dumps(meta))
        keys = _read_keys()
        (work / "cles.env").write_text("".join(f"{k}={v}\n" for k, v in keys.items() if v))
        bundle = dest_dir / f"config-{int(time.time())}.tar.gz"
        with tarfile.open(bundle, "w:gz") as tar:
            for name in ("hyperlite.db", "cles.env", "meta.json"):
                tar.add(work / name, arcname=name)
        bundle.chmod(0o600)
        return bundle, meta
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --- Sending --------------------------------------------------------------------------------------------------------


def push(node, bundle):
    """Send a bundle to a node: into REMOTE_DIR/incoming, then renamed over latest (the previous one kept)."""
    from app.core.cluster import node_ssh_options

    target = f"{node['ssh_user']}@{node['hostname']}"
    opts = node_ssh_options(["-o", "ConnectTimeout=10"])
    port = str(node["ssh_port"])
    run = lambda args, timeout: subprocess.run(args, capture_output=True, text=True, timeout=timeout)  # noqa: E731
    r = run(["ssh", *opts, "-p", port, target, f"mkdir -p -m 700 {REMOTE_DIR} && chmod 700 {REMOTE_DIR}"], 20)
    if r.returncode != 0:
        raise CopyError(f"Cannot prepare {REMOTE_DIR}: {r.stderr.strip()[:200]}")
    r = run(["scp", *opts, "-P", port, str(bundle), f"{target}:{REMOTE_DIR}/incoming.tar.gz"], 300)
    if r.returncode != 0:
        raise CopyError(f"Copy failed: {r.stderr.strip()[:200]}")
    swap = (
        f"cd {REMOTE_DIR} && chmod 600 incoming.tar.gz && "
        "{ [ ! -f latest.tar.gz ] || mv -f latest.tar.gz previous.tar.gz; } && mv -f incoming.tar.gz latest.tar.gz"
    )
    r = run(["ssh", *opts, "-p", port, target, swap], 20)
    if r.returncode != 0:
        raise CopyError(f"Cannot install the copy: {r.stderr.strip()[:200]}")


def _record(node, statut, taille=None, empreinte=None, erreur=None):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO config_copies (node, copie_le, statut, taille, empreinte, erreur) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(node) DO UPDATE SET copie_le = excluded.copie_le, statut = excluded.statut, "
            "taille = COALESCE(excluded.taille, config_copies.taille), "
            "empreinte = COALESCE(excluded.empreinte, config_copies.empreinte), erreur = excluded.erreur",
            (node, _now().isoformat(), statut, taille, empreinte, erreur),
        )
        db.commit()


def status():
    with database.get_conn() as db:
        return [dict(r) for r in db.execute("SELECT * FROM config_copies ORDER BY node")]


def copy_now(force=False, username="system"):
    """Send the configuration to every online node that needs it (digest changed or copy too old; all with force)."""
    from app.core.audit import log_action
    from app.core.cluster import list_nodes

    nodes = [n for n in list_nodes() if n["statut"] != "hors_ligne"]
    if not nodes:
        return []
    digest = config_digest()
    last = {r["node"]: r for r in status()}
    due = []
    for node in nodes:
        prev = last.get(node["name"])
        stale = prev is None or prev["statut"] != "ok" or prev["empreinte"] != digest
        if not stale:
            age = (_now() - datetime.fromisoformat(prev["copie_le"])).total_seconds()
            stale = age >= COPY_MAX_AGE_S
        if force or stale:
            due.append(node)
    if not due:
        return []
    staging = database.DB_PATH.parent / "data" / "config-copy"
    staging.mkdir(parents=True, exist_ok=True)
    staging.chmod(0o700)
    bundle, meta = build_bundle(staging)
    results = []
    try:
        for node in due:
            try:
                push(node, bundle)
                _record(node["name"], "ok", bundle.stat().st_size, meta["empreinte"])
                results.append({"node": node["name"], "statut": "ok"})
                continue
            except CopyError as e:
                error = e.message[:300]
            except (OSError, subprocess.SubprocessError):
                # The OS error text stays in the service log; the API gets a sentence.
                logger.warning("Configuration copy to %s failed", node["name"], exc_info=True)
                error = "ssh or scp could not run or timed out (see the service log)"
            _record(node["name"], "echec", erreur=error)
            log_action(username, "config_copy", node["name"], "echec", error)
            results.append({"node": node["name"], "statut": "echec", "erreur": error})
    finally:
        bundle.unlink(missing_ok=True)
    return results


def _loop():
    while True:
        try:
            copy_now()
        except Exception as e:  # never let one round stop the copies
            print(f"[config-copy] round failed: {e!r}", flush=True)
        time.sleep(COPY_CHECK_S)


def start_config_copy():
    thread = threading.Thread(target=_loop, daemon=True, name="config-copy")
    thread.start()
    return thread


# --- Promotion (run by hand on the node that takes over) ------------------------------------------------------------


def controller_answers(host, timeout=5):
    """True when the old controller still answers on its dashboard port or SSH."""
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # a liveness probe of our own old controller, self-signed certificate
    try:
        with urllib.request.urlopen(f"https://{host}:8000/health", timeout=timeout, context=ctx):
            return True
    except (OSError, ValueError) as e:  # refused, timed out, TLS or HTTP error: the dashboard does not answer
        print(f"Old controller dashboard: no answer ({e})")
    try:
        with socket.create_connection((host, 22), timeout=timeout):
            return True
    except OSError:
        return False


def read_bundle(path):
    """(meta, extracted directory) of a bundle; the caller removes the directory."""
    work = Path(tempfile.mkdtemp())
    with tarfile.open(path) as tar:
        tar.extractall(work, filter="data")
    meta = json.loads((work / "meta.json").read_text())
    return meta, work


def install(work, meta, self_names, db_path=None, env_path=None, old_label=None):
    """Put a bundle's database and keys in place, then make it this node's view: the rows naming this node become
    "local", and the old controller's "local" rows are renamed to old_label (the old controller's host name)."""
    db_path = Path(db_path or database.DB_PATH)
    env_path = Path(env_path or _env_path())
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    if db_path.exists():
        shutil.copy2(db_path, db_path.with_name(f"{db_path.name}.before-promote-{stamp}"))
        for suffix in ("-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    shutil.copy2(work / "hyperlite.db", db_path)
    db_path.chmod(0o600)

    keys = dict(line.split("=", 1) for line in (work / "cles.env").read_text().splitlines() if "=" in line)
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    kept = [line for line in lines if line.partition("=")[0].strip() not in keys]
    env_path.write_text("\n".join(kept + [f"{k}={v}" for k, v in keys.items()]) + "\n")
    env_path.chmod(0o600)

    old_label = old_label or meta.get("controleur") or "old-controller"
    conn = sqlite3.connect(db_path)
    try:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self_rows = (
            [r[0] for r in conn.execute("SELECT name FROM nodes") if r[0] in self_names] if "nodes" in existing else []
        )
        for table in ("ha_protected_vms", "node_maintenance", "node_fencing", "config_copies"):
            if table not in existing:
                continue
            conn.execute(f"UPDATE {table} SET node = ? WHERE node = 'local'", (old_label,))  # noqa: S608
            for name in self_rows:
                conn.execute(f"UPDATE {table} SET node = 'local' WHERE node = ?", (name,))  # noqa: S608
        for name in self_rows:
            conn.execute("DELETE FROM nodes WHERE name = ?", (name,))
        conn.commit()
    finally:
        conn.close()
    return {
        "sauvegarde": f"{db_path.name}.before-promote-{stamp}",
        "ancien_controleur": old_label,
        "noeud_local": self_rows,
    }


def _systemctl(*args):
    return subprocess.run(["systemctl", *args], capture_output=True, text=True, check=False)


def promote_cli(argv=None):
    parser = argparse.ArgumentParser(
        prog="hyperlite promote",
        description="Make this node the Hyperlite controller from the last configuration copy.",
    )
    parser.add_argument("--copy", default=f"{REMOTE_DIR}/latest.tar.gz", help="the bundle to install")
    parser.add_argument(
        "--old-controller", help="host name or address of the old controller (default: the one in the copy)"
    )
    parser.add_argument(
        "--self-name", action="append", default=[], help="this node's name in the cluster (default: its host names)"
    )
    parser.add_argument("--force", action="store_true", help="promote even though the old controller still answers")
    parser.add_argument("--yes", action="store_true", help="do not ask for the confirmation")
    args = parser.parse_args(argv)

    if not Path(args.copy).is_file():
        print(f"No configuration copy at {args.copy}.", file=sys.stderr)
        return 2
    meta, work = read_bundle(args.copy)
    try:
        age_min = int((_now() - datetime.fromisoformat(meta["cree_le"])).total_seconds() // 60)
        old = args.old_controller or meta.get("controleur")
        print(f"Copy from controller {meta.get('controleur')}, made {age_min} min ago.")
        if old and controller_answers(old) and not args.force:
            print(
                f"The old controller {old} still answers. Two controllers would both act on the cluster (HA, backups, "
                "jobs). Stop it first, or pass --force if you are sure it is gone for good.",
                file=sys.stderr,
            )
            return 3
        if not args.yes:
            typed = input(f"Type the old controller's name ({meta.get('controleur')}) to promote this node: ").strip()
            if typed != meta.get("controleur"):
                print("Not confirmed, nothing changed.", file=sys.stderr)
                return 4
        _systemctl("stop", SERVICE)
        self_names = set(args.self_name) or {socket.gethostname(), socket.getfqdn()}
        result = install(work, meta, self_names, old_label=old)
        _systemctl("start", SERVICE)
        print(f"Promoted. Previous database kept as {result['sauvegarde']}.")
        print(
            f"The old controller's VMs and settings now refer to it as node '{result['ancien_controleur']}': register "
            "it as a node under that name when it comes back, instead of starting its own Hyperlite."
        )
        print(f"Open the dashboard at https://{socket.getfqdn()}:8000/")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "promote":
        sys.exit(promote_cli(sys.argv[2:]))
    print("usage: python -m app.core.config_copy promote [--copy PATH] [--old-controller HOST] [--force] [--yes]")
    sys.exit(2)
