"""Shadow mode of hyperlite-cfs (docs/design/hyperlite-cfs.md, section 8, phase C).

Every write to a configuration table (tables.py) is recorded by SQLite triggers in the cfs_outbox table, in the same
transaction as the write, whatever code made it. A background thread copies what the outbox lists into the local
hyperlite-cfs daemon, re-reading each row from SQLite, so what is copied is what SQLite holds; an entry leaves the
outbox only once the daemon took it, so a daemon that was down is caught up on as soon as it answers again.

SQLite stays the source of truth: a copy never fails or slows the write that caused it. report() compares the two
entry by entry. Rows are stored as JSON at /db/<table>/<primary key...>, or /priv/db/... for tables holding secrets.
"""

import base64
import contextlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime

from app.core.cfs_client import (
    DEFAULT_SOCKET,
    FORBIDDEN,
    CfsClient,
    CfsError,
    NotFound,
    ReadOnly,
    Synchronising,
    Uncertain,
)
from app.repositories.cfs import ids
from app.repositories.cfs.tables import TABLES

logger = logging.getLogger(__name__)

# The daemon is local; a copy waits at most this long, so a stuck daemon slows a write by seconds, not minutes.
TIMEOUT_S = 2.0
EXAMPLES = 20  # paths listed per kind of difference in the report

_PLAIN = re.compile(r"^[A-Za-z0-9-][A-Za-z0-9._-]*$")


DISABLED = "Shadow mode is off: turn it on in Administration > Replicated configuration"


class ShadowDisabled(Exception):
    pass


# Turned on from Administration > Replicated configuration (app_settings), or forced by HYPERLITE_CFS_SHADOW=1.
SETTING = "cfs_shadow"
SERVICE = "hyperlite-cfs.service"
BINARY = "/usr/local/sbin/hyperlite-cfs"  # installed by the package (scripts/build-cfs.sh)
START_WAIT_S = 10


class ShadowError(Exception):
    """A switch that could not be made; the message is a fixed sentence, safe to show."""


def _settings():
    from app.repositories.sqlite.settings import SqliteSettingsStore

    return SqliteSettingsStore()


def forced():
    return os.environ.get("HYPERLITE_CFS_SHADOW", "") == "1"


def enabled():
    return forced() or _settings().app_setting(SETTING) == "1"


def socket_path():
    return os.environ.get("HYPERLITE_CFS_SOCKET") or DEFAULT_SOCKET


def component(name):
    """A path component for any name: the name itself when the daemon accepts it as it is, otherwise '_' followed by
    its UTF-8 bytes in hex. A name that starts with '_' is always encoded, so an encoded name never equals a real one."""
    name = str(name)
    if _PLAIN.match(name):
        return name
    return "_" + name.encode().hex()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _db():
    from app.core.database import get_conn

    return get_conn()


def _value(v):
    # JSON has no bytes; a BLOB column (a security key's public key) is kept as base64 under a marker key.
    return {"$b64": base64.b64encode(v).decode()} if isinstance(v, bytes) else v


class _Table:
    """One mirrored table: its rows at <prefix>/<primary key...>, as JSON of the columns that are configuration."""

    def __init__(self, name, spec, columns, pk):
        self.name = name
        self.prefix = ("/priv/db/" if spec.get("priv") else "/db/") + name
        self.pk = pk
        volatile = spec.get("volatile", set())
        self.columns = [c for c in columns if c not in volatile]
        self.local_rows = spec.get("local_rows", {})
        self.fill = spec.get("fill", {})

    def path(self, key):
        return "/".join([self.prefix, *(component(k) for k in key)])

    def _select(self):
        return "SELECT " + ", ".join(f'"{c}"' for c in self.columns) + f' FROM "{self.name}"'  # noqa: S608

    def _local(self, row):
        return any(row[c] in values for c, values in self.local_rows.items())

    def _data(self, row):
        return _encode({c: _value(row[c]) for c in self.columns})

    def entry(self, key):
        """(path, data) of one row as SQLite now holds it; data is None when the row is gone or stays local."""
        where = " AND ".join(f'"{c}" = ?' for c in self.pk)
        with _db() as db:
            row = db.execute(f"{self._select()} WHERE {where}", tuple(key)).fetchone()
        return self.path(key), (self._data(row) if row and not self._local(row) else None)

    def all(self, db=None):
        if db is None:
            with _db() as own:
                rows = own.execute(self._select()).fetchall()
        else:
            rows = db.execute(self._select()).fetchall()
        return {self.path([r[c] for c in self.pk]): self._data(r) for r in rows if not self._local(r)}


def _schema(db):
    """{table: (columns, primary key)} of the listed tables this database has."""
    out = {}
    for name in TABLES:
        info = db.execute(f'PRAGMA table_info("{name}")').fetchall()
        if info:
            pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
            out[name] = ([r[1] for r in info], pk)
    return out


def tables():
    """{name: _Table} of the mirrored tables, from the live schema (a column added by a migration is included)."""
    with _db() as db:
        return {name: _Table(name, TABLES[name], cols, pk) for name, (cols, pk) in _schema(db).items()}


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def install_triggers(db):
    """Create the outbox and, on each listed table, the triggers that record every inserted, updated (configuration
    columns only) and deleted row in it. Run by init_db in its transaction; existing cfs_ triggers are replaced, so a
    change of tables.py or of a table's columns takes effect at the next start."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS cfs_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL, pk TEXT NOT NULL)"
    )
    for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'cfs!_%' ESCAPE '!'"
    ):
        db.execute(f'DROP TRIGGER "{name}"')
    for name, (columns, pk) in _schema(db).items():
        table = _Table(name, TABLES[name], columns, pk)

        def record(alias, table=table, name=name):
            keys = ", ".join(f'{alias}."{c}"' for c in table.pk)
            # Rows that stay on each node (tables.py, local_rows) are not recorded at all.
            local = " AND ".join(
                f'{alias}."{c}" NOT IN ({", ".join(_quote(v) for v in values)})'
                for c, values in table.local_rows.items()
            )
            # Table and column names come from tables.py and the schema, never from a request.
            return f"INSERT INTO cfs_outbox (tbl, pk) SELECT '{name}', json_array({keys})" + (
                f" WHERE {local};" if local else ";"
            )

        tracked = ", ".join(f'"{c}"' for c in table.columns)
        db.execute(f'CREATE TRIGGER "cfs_{name}_ins" AFTER INSERT ON "{name}" BEGIN {record("NEW")} END')
        db.execute(
            f'CREATE TRIGGER "cfs_{name}_upd" AFTER UPDATE OF {tracked} ON "{name}" '
            f"BEGIN {record('OLD')} {record('NEW')} END"
        )
        db.execute(f'CREATE TRIGGER "cfs_{name}_del" AFTER DELETE ON "{name}" BEGIN {record("OLD")} END')


class _Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.copies = 0
        self.failures = 0
        self.last_error = None
        self.last_error_at = None


_stats = _Stats()
_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None or _client.path != socket_path():
            _client = CfsClient(socket_path(), timeout=TIMEOUT_S)
        return _client


def describe(error):
    """A fixed sentence per kind of failure. The report and the API answers reach the browser; the exception's own text
    stays in the service log."""
    if isinstance(error, ReadOnly):
        return "hyperlite-cfs has no quorum: it is read-only"
    if isinstance(error, Synchronising):
        return "hyperlite-cfs is synchronising with the other nodes"
    if isinstance(error, Uncertain):
        return "hyperlite-cfs did not confirm the change (a member left meanwhile)"
    if isinstance(error, CfsError):
        return "hyperlite-cfs refused the change (details in the service log)"
    if isinstance(error, (FileNotFoundError, ConnectionRefusedError)):
        return "hyperlite-cfs is not running: nothing answers on its socket"
    if isinstance(error, TimeoutError):
        return f"hyperlite-cfs did not answer within {TIMEOUT_S:g} s"
    return "hyperlite-cfs could not be reached (details in the service log)"


def _failed(what, error):
    with _stats.lock:
        _stats.failures += 1
        _stats.last_error = f"{what}: {describe(error)}"
        _stats.last_error_at = datetime.now(UTC).isoformat()
    logger.warning("hyperlite-cfs shadow: %s not copied: %s", what, error)


def _write(client, path, data):
    if data is None:
        with contextlib.suppress(NotFound):  # already absent: the state wanted
            client.delete(path)
    else:
        client.put(path, data)


def pending():
    with _db() as db:
        return db.execute("SELECT COUNT(*) FROM cfs_outbox").fetchone()[0]


def drain(limit=500):
    """Copy what the outbox lists, oldest first; returns how many entries were copied. Stops at the first failure and
    keeps the rest for the next call, so the daemon receives the changes in the order SQLite made them."""
    if not enabled():
        with _db() as db:  # shadow mode off: nothing is owed to the daemon
            db.execute("DELETE FROM cfs_outbox")
            db.commit()
        return 0
    with _db() as db:
        rows = db.execute("SELECT id, tbl, pk FROM cfs_outbox ORDER BY id LIMIT ?", (limit,)).fetchall()
    if not rows:
        return 0
    known = tables()
    # A row changed several times is copied once, in the place of its last change.
    last = {}
    for r in rows:
        last.pop((r["tbl"], r["pk"]), None)
        last[(r["tbl"], r["pk"])] = r["id"]
    done = []
    copied = 0
    try:
        client = _get_client()
    except (CfsError, OSError) as e:
        _failed("hyperlite-cfs", e)
        return 0
    for (tbl, pk), _ in last.items():
        table = known.get(tbl)
        if table is not None:
            path, data = table.entry(json.loads(pk))
            try:
                _write(client, path, data)
            except (CfsError, OSError) as e:
                _failed(path, e)
                break
            copied += 1
        done += [r["id"] for r in rows if (r["tbl"], r["pk"]) == (tbl, pk)]
    if done:
        with _db() as db:
            db.executemany("DELETE FROM cfs_outbox WHERE id = ?", [(i,) for i in done])
            db.commit()
    with _stats.lock:
        _stats.copies += copied
    return copied


def _loop():
    delay = 1.0
    while True:
        time.sleep(delay)
        try:
            before = _stats.failures
            drain()
            delay = 1.0 if _stats.failures == before else min(delay * 2, 30.0)
        except Exception:  # the thread must survive anything a database or daemon can throw at it
            logger.exception("hyperlite-cfs shadow: the copy loop failed; retrying")
            delay = 30.0


def start_shadow_copy():
    threading.Thread(target=_loop, daemon=True, name="cfs-shadow").start()


def _read_tree(client, prefix):
    """{path: data} of every file under `prefix`."""
    out = {}
    try:
        children = client.list(prefix)
    except NotFound:
        return out
    for child in children:
        path = f"{prefix}/{child.name}"
        if child.is_dir:
            out.update(_read_tree(client, path))
        else:
            try:
                out[path] = client.get(path).data
            except NotFound:
                continue  # deleted between the listing and the read: absent, as the next report will show
    return out


def _sync(client, domain):
    expected = domain.all()
    actual = _read_tree(client, domain.prefix)
    written = deleted = 0
    for path, data in expected.items():
        if actual.get(path) != data:
            _write(client, path, data)
            written += 1
    for path in actual.keys() - expected.keys():
        _write(client, path, None)
        deleted += 1
    return written, deleted


def seed():
    """Make the daemon hold exactly what SQLite holds, for every mirrored domain: the first copy when shadow mode is
    turned on, or a repair after the daemon was down. Raises ShadowDisabled, CfsError or OSError."""
    if not enabled():
        raise ShadowDisabled(DISABLED)
    client = _get_client()
    written = deleted = 0
    for table in tables().values():
        w, d = _sync(client, table)
        written += w
        deleted += d
    if client.status().mode == "cluster":
        # The tree now holds this node's rows: ids handed out from now on start above them (ids.py).
        with _db() as db:
            ids.set_offset(db, client)
    from app.repositories.cfs import inbound  # imports this module

    inbound.mark_applied()
    return {"ecrits": written, "supprimes": deleted}


def _systemctl(*args):
    try:
        done = subprocess.run(["systemctl", *args, SERVICE], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("systemctl %s %s: %s", " ".join(args), SERVICE, e)
        return False
    if done.returncode:
        logger.warning("systemctl %s %s failed: %s", " ".join(args), SERVICE, done.stderr.strip())
    return done.returncode == 0


def turn_on():
    """Start the daemon (local mode unless /etc/default/hyperlite-cfs says otherwise), remember that shadow mode is on,
    and make the first copy. Raises ShadowError with a sentence that says what to do."""
    if not os.path.exists(BINARY):
        raise ShadowError(
            "hyperlite-cfs is not installed on this node: its build failed at the last upgrade (see the upgrade log)"
        )
    if not _systemctl("enable", "--now"):
        raise ShadowError("The hyperlite-cfs service did not start: see systemctl status hyperlite-cfs")
    deadline = time.monotonic() + START_WAIT_S
    while True:
        try:
            _get_client().status()
            break
        except (CfsError, OSError) as e:
            if time.monotonic() > deadline:
                logger.warning("hyperlite-cfs started but does not answer: %s", e)
                raise ShadowError(
                    "hyperlite-cfs started but does not answer: see journalctl -u hyperlite-cfs"
                ) from None
            time.sleep(0.2)
    _settings().set_app_setting(SETTING, "1")
    try:
        return seed()
    except (CfsError, OSError) as e:
        logger.warning("hyperlite-cfs shadow: first copy failed: %s", e)
        raise ShadowError(f"Shadow mode is on, but the first copy did not complete: {describe(e)}") from None


def turn_off():
    """Stop copying and stop the daemon. Its database stays in /var/lib/hyperlite-cfs for the next time."""
    if forced():
        raise ShadowError(
            "Shadow mode is forced by HYPERLITE_CFS_SHADOW=1 in .env: remove that line and restart Hyperlite"
        )
    _settings().set_app_setting(SETTING, "0")
    if not _systemctl("disable", "--now"):
        raise ShadowError(
            "Shadow mode is off, but the hyperlite-cfs service did not stop: see systemctl status hyperlite-cfs"
        )


def _compare(expected, actual):
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    different = sorted(p for p in expected.keys() & actual.keys() if expected[p] != actual[p])
    return {
        "entrees": len(expected),
        "manquants": len(missing),
        "en_trop": len(extra),
        "differents": len(different),
        "exemples": {"manquants": missing[:EXAMPLES], "en_trop": extra[:EXAMPLES], "differents": different[:EXAMPLES]},
    }


def _compare_table(client, table):
    try:
        return _compare(table.all(), _read_tree(client, table.prefix))
    except CfsError as e:
        if e.status != FORBIDDEN:
            raise
        # /priv is served to root only: a Hyperlite not running as root (a development checkout) cannot compare it.
        out = _compare({}, {})
        out.update(entrees=len(table.all()), illisible=True)
        return out


def report():
    """Shadow mode's state and, when the daemon answers, the differences between SQLite and the daemon per domain."""
    from app.repositories.cfs import inbound  # imports this module

    with _stats.lock:
        out = {
            "actif": enabled(),
            "force": forced(),
            "installe": os.path.exists(BINARY),
            "socket": socket_path(),
            "copies": _stats.copies,
            "echecs": _stats.failures,
            "derniere_erreur": _stats.last_error,
            "derniere_erreur_le": _stats.last_error_at,
            "en_attente": pending(),
        }
    try:
        client = _get_client()
        status = client.status()
        domains = {name: _compare_table(client, t) for name, t in tables().items()}
    except (CfsError, OSError) as e:
        logger.warning("hyperlite-cfs shadow report: %s", e)
        out.update(joignable=False, erreur=describe(e))
        return out
    out.update(
        joignable=True,
        demon={"mode": status.mode, "quorum": status.quorate, "version": status.version, "entrees": status.entries},
        domaines=domains,
        ecarts=sum(d["manquants"] + d["en_trop"] + d["differents"] for d in domains.values()),
        reception=inbound.report(),
    )
    return out
