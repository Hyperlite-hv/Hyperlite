"""Is this node's database sound: checked at start and once a day, said in the dashboard and the notifications when not.

SQLite reports a damaged file only to the statement that reads a damaged page. A hard stop of a host whose disk caches
writes is enough to damage the indexes of the busiest tables, and a background loop that reads them then logs
`database disk image is malformed` at every round while the dashboard shows nothing. So:

- `PRAGMA quick_check` runs at start and every CHECK_INTERVAL_S. It reads every page, but does not compare an index
  with its table;
- the background loops report their rounds (`failed`, `succeeded`). REPEAT corruption errors in a row in one loop run
  the full `PRAGMA integrity_check`, which does compare them. A loop that keeps failing while both checks pass is shown
  as well: the check is what can be wrong then;
- a damaged database raises a notification and a banner on the dashboard (`summary()`, in the node's summary), with
  the repair that fits what is damaged (`repair` in the summary):
  - "reindex": index entries that disagree with their table, on sound pages. `REINDEX` rebuilds them in place, with
    the service running (POST /host/database/repair);
  - "rebuild": damaged index pages, or pages no tree uses. SQLite cannot free a damaged page (REINDEX, DROP INDEX and
    VACUUM all fail on it), so the file is rebuilt from its tables, read without their indexes, with the service
    stopped (`python -m app.core.db_health rebuild`). A damaged history table (HISTORY_TABLES) is emptied by the
    rebuild when asked to (`--drop-history`): the history is lost, the configuration is kept;
  - "restore": a configuration table is damaged. Nothing in the file can be trusted for it, and no command restores
    it yet: docs/database-health.md says what can be done, by hand.
"""

import argparse
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.core import database

logger = logging.getLogger(__name__)

CHECK_INTERVAL_S = 24 * 3600
# Corruption errors in a row in one loop before the full check runs: one can be a page read while the disk was full.
REPEAT = 3
# How many lines of a check's report are kept and shown: a damaged index page alone gives thousands.
SHOWN = 20
# SQLite's primary result codes for a damaged file (sqlite3.h): SQLITE_CORRUPT, SQLITE_NOTADB.
CORRUPTION_CODES = {11, 26}
# What a node can lose without losing its configuration: what happened, and the measures. A rebuild may empty them.
HISTORY_TABLES = (
    "audit_log",
    "tasks",
    "task_logs",
    "job_run_logs",
    "metrics_samples",
    "storage_samples",
    "node_live",
)
SERVICE = "hyperlite"

_lock = threading.Lock()
_stop = threading.Event()
# The last check's verdict, and the loops that keep failing: what summary() reads.
_check = {"status": "unknown", "checked_at": None, "full": False}
_loops = {}
_published = "unknown"


def _now():
    return datetime.now(UTC).isoformat(timespec="seconds")


def is_corruption(exc):
    """True when `exc`, or what caused it, is SQLite saying the file is damaged (not locked, full or constrained)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        # The code SQLite gave, never the text: an extended code keeps the primary one in its low byte.
        code = getattr(exc, "sqlite_errorcode", None) if isinstance(exc, sqlite3.DatabaseError) else None
        if code is not None and code & 0xFF in CORRUPTION_CODES:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _lines(rows):
    """The report's lines: SQLite gives several in one row, under a heading naming the schema."""
    out = []
    for (row,) in rows:
        out.extend(line for line in str(row).splitlines() if line.strip() and not line.startswith("***"))
    return out


TREE = re.compile(r"^Tree (\d+) page")
ENTRY = re.compile(r"(?:missing from index|wrong # of entries in index|non-unique entry in UNIQUE index) (\S+)")


def inspect(path=None, full=False):
    """Check the database at `path` (this node's by default) and say what is damaged:
    {"ok", "kind", "problems", "problem_count", "indexes", "tables"}. kind: None when sound, else "index_entries"
    (wrong entries on sound pages), "index_pages" (damaged index pages, or pages no tree uses), "tables", or
    "unreadable" (the check itself could not read the file)."""
    path = path or database.DB_PATH
    # Read-only: a check never writes into the file it suspects, not even the checkpoint of its log.
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=30)
    try:
        try:
            rows = conn.execute("PRAGMA integrity_check" if full else "PRAGMA quick_check").fetchall()
            roots = {r: (t, n) for t, n, r in conn.execute("SELECT type, name, rootpage FROM sqlite_master")}
        except sqlite3.DatabaseError as e:
            if not is_corruption(e):
                raise
            return _verdict("unreadable", [str(e)], set(), set())
    finally:
        conn.close()
    problems = _lines(rows)
    if problems == ["ok"]:
        return _verdict(None, [], set(), set())
    indexes, tables, pages = set(), set(), False
    for line in problems:
        if m := TREE.match(line):
            kind, name = roots.get(int(m.group(1)), ("table", f"tree {m.group(1)}"))
            (indexes if kind == "index" else tables).add(name)
            pages = pages or kind == "index"
        elif m := ENTRY.search(line):
            indexes.add(m.group(1))
        else:  # a page no tree uses, a damaged free list: only a rebuild of the file clears them
            pages = True
    kind = "tables" if tables else "index_pages" if pages else "index_entries"
    return _verdict(kind, problems, indexes, tables)


def _verdict(kind, problems, indexes, tables):
    return {
        "ok": kind is None,
        "kind": kind,
        "problems": problems[:SHOWN],
        "problem_count": len(problems),
        "indexes": sorted(indexes),
        "tables": sorted(tables),
    }


def repair_for(verdict):
    """The repair that fits a check's verdict: "reindex", "rebuild", "restore", or None when sound."""
    kind = verdict.get("kind")
    if kind is None:
        return None
    if kind == "index_entries":
        return "reindex"
    # Unreadable: which pages are damaged is not known, and a rebuild that cannot read a table stops without a change.
    if kind in ("index_pages", "unreadable") or (kind == "tables" and set(verdict["tables"]) <= set(HISTORY_TABLES)):
        return "rebuild"
    return "restore"


def check(full=False):
    """Check this node's database now, record the verdict and say it when it changed. Returns summary()."""
    try:
        verdict = inspect(full=full)
    except sqlite3.Error as e:  # locked for 30 s, or no file: not a verdict on the file's state
        logger.warning("Database check not run: %s", e)
        return summary()
    with _lock:
        _check.clear()
        _check.update(verdict, status="ok" if verdict["ok"] else "damaged", checked_at=_now(), full=full)
        _check["repair"] = repair_for(verdict)
    _publish()
    return summary()


def failed(source, exc):
    """A background loop's round failed with `exc`. Repeated corruption errors in a row run the full check."""
    if not is_corruption(exc):
        return
    with _lock:
        entry = _loops.setdefault(source, {"errors": 0, "since": _now()})
        entry["errors"] += 1
        entry["error"] = str(exc)
        count = entry["errors"]
    if count == REPEAT:
        logger.error("The %s loop failed %d times in a row on a damaged database: checking it", source, count)
        check(full=True)


def succeeded(source):
    """A background loop's round went through: its earlier errors no longer count."""
    with _lock:
        gone = _loops.pop(source, None)
    if gone and gone["errors"] >= REPEAT:
        _publish()


def summary():
    """This node's database state, for the dashboard: status "ok", "damaged" (a check found damage), "failing" (no
    check found any, but a loop keeps hitting corruption errors) or "unknown" (not checked yet)."""
    with _lock:
        out = {k: v for k, v in _check.items() if k != "ok"}
        loops = {name: dict(e) for name, e in _loops.items() if e["errors"] >= REPEAT}
    out["loops"] = loops
    if out["status"] != "damaged" and loops:
        out["status"] = "failing"
    return out


def _publish():
    """Notify once when the database turns bad, and once when it is sound again."""
    global _published
    state = summary()
    with _lock:
        before, _published = _published, state["status"]
    bad = ("damaged", "failing")
    if state["status"] in bad and before not in bad:
        detail = _describe(state)
        logger.error("Damaged database: %s", detail)
        _log("system", "database_check", "failed", detail)
    elif before in bad and state["status"] == "ok":
        _log("system", "database_check", "success", "the database is sound again")


def _describe(state):
    if state["status"] == "failing":
        return "; ".join(
            f"the {name} loop failed {e['errors']} times: {e['error']}" for name, e in state["loops"].items()
        )
    what = {
        "index_entries": "index entries disagree with their tables",
        "index_pages": "index pages are damaged",
        "tables": "tables are damaged",
        "unreadable": "the file cannot be read",
    }[state["kind"]]
    named = ", ".join(state.get("tables") or state.get("indexes") or [])
    return f"{what}{f' ({named})' if named else ''}; repair: {state['repair']}"


def _log(username, action, result, detail):
    from app.core import self_node
    from app.core.audit import log_action

    try:
        log_action(username, action, f"{self_node.name()} database", result, detail)
    except Exception as e:  # the audit table itself may be the damaged one: the service log keeps it
        logger.error("Database check not recorded (%s): %s", e, detail)


class RepairRefused(Exception):
    """The online repair does not fit what is damaged: the summary's `repair` says what does."""


def repair(username):
    """Rebuild the indexes whose entries disagree with their tables, with the service running. Refused for any other
    damage. Returns summary() after the full check that follows."""
    state = check(full=True)
    if state["status"] != "damaged":
        raise RepairRefused("the check finds no damage to repair")
    if state["repair"] != "reindex":
        raise RepairRefused(f"this damage needs a {state['repair']}, not an online repair")
    conn = sqlite3.connect(database.DB_PATH, timeout=30)
    error = None
    try:
        for name in state["indexes"]:
            conn.execute(f"REINDEX {_quote(name)}")
        conn.commit()
    except sqlite3.DatabaseError as e:
        conn.rollback()
        error = str(e)
    finally:
        conn.close()
    after = check(full=True)
    sound = after["status"] == "ok"
    detail = f"REINDEX {', '.join(state['indexes'])}" + (f": {error}" if error else "")
    _log(username, "repair_database", "success" if sound else "failed", detail)
    return after


def forget():
    """Back to "not checked yet": for the tests."""
    global _published
    with _lock:
        _check.clear()
        _check.update(status="unknown", checked_at=None, full=False)
        _loops.clear()
        _published = "unknown"


def _loop():
    while not _stop.is_set():
        try:
            check()
        except Exception:  # the loop must outlive one check
            logger.exception("Database check failed")
        _stop.wait(CHECK_INTERVAL_S)


def start():
    thread = threading.Thread(target=_loop, daemon=True, name="db-health")
    thread.start()
    return thread


# --- Rebuild (run by hand, with the service stopped) ----------------------------------------------------------------


class RebuildFailed(Exception):
    pass


def rebuild(path, drop_history=False):
    """Write a new file from the tables of the database at `path`, read without their indexes, and put it in place of
    the old one, kept next to it. drop_history: a history table that cannot be read is left empty instead of stopping
    the rebuild. Returns {"kept", "emptied", "rows"}."""
    path = Path(path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    new = path.with_name(f"{path.name}.rebuild-{stamp}")
    src = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    dst = sqlite3.connect(new)
    emptied, rows = [], {}
    try:
        schema = src.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        ).fetchall()
        dst.execute(f"PRAGMA user_version = {int(src.execute('PRAGMA user_version').fetchone()[0])}")
        tables = [name for kind, name, _ in schema if kind == "table"]
        for kind, _, sql in schema:
            if kind == "table":
                dst.execute(sql)
        for name in tables:
            try:
                rows[name] = _copy(src, dst, name)
            except sqlite3.DatabaseError as e:
                if not (drop_history and name in HISTORY_TABLES and is_corruption(e)):
                    raise RebuildFailed(f"table {name} cannot be read: {e}") from e
                dst.execute(f"DELETE FROM {_quote(name)}")  # noqa: S608 - names from sqlite_master, quoted
                emptied.append(name)
        if tables and src.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
            # AUTOINCREMENT's counters: a deleted row's id must not come back.
            dst.execute("DELETE FROM sqlite_sequence")
            dst.executemany(
                "INSERT INTO sqlite_sequence VALUES (?, ?)", src.execute("SELECT name, seq FROM sqlite_sequence")
            )
        for kind, _, sql in schema:
            if kind != "table":
                dst.execute(sql)
        dst.commit()
        result = [r[0] for r in dst.execute("PRAGMA integrity_check").fetchall()]
        if result != ["ok"]:
            raise RebuildFailed(f"the rebuilt file does not pass its check: {result[:3]}")
        dst.execute("PRAGMA journal_mode=WAL")
    except BaseException:
        dst.close()
        new.unlink(missing_ok=True)
        raise
    finally:
        src.close()
    dst.close()
    kept = path.with_name(f"{path.name}.damaged-{stamp}")
    for suffix in ("", "-wal", "-shm"):
        old = Path(f"{path}{suffix}")
        if old.exists():
            os.replace(old, f"{kept}{suffix}")
    os.chmod(new, 0o600)
    os.replace(new, path)
    return {"kept": str(kept), "emptied": emptied, "rows": rows}


def _copy(src, dst, name):
    """Copy a table's rows through its own tree only (NOT INDEXED): its indexes may be what is damaged."""
    cur = src.execute(f"SELECT * FROM {_quote(name)} NOT INDEXED")  # noqa: S608 - names from sqlite_master, quoted
    columns = ", ".join(_quote(d[0]) for d in cur.description)
    insert = f"INSERT INTO {_quote(name)} ({columns}) VALUES ({', '.join('?' * len(cur.description))})"  # noqa: S608 - names from sqlite_master, quoted
    count = 0
    while batch := cur.fetchmany(1000):
        dst.executemany(insert, batch)
        count += len(batch)
    return count


def _service_active():
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], check=False).returncode == 0
    except FileNotFoundError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.core.db_health", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check the database; exit 1 when it is damaged")
    c.add_argument("--full", action="store_true", help="also compare every index with its table (slower)")
    r = sub.add_parser("rebuild", help="rebuild the database from its tables, with the service stopped")
    r.add_argument("--drop-history", action="store_true", help="empty a history table that cannot be read")
    for p in (c, r):
        p.add_argument("--db", default=str(database.DB_PATH), help="the database file (default: this node's)")
    args = parser.parse_args(argv)

    verdict = inspect(args.db, full=getattr(args, "full", True))
    if args.cmd == "check":
        if verdict["ok"]:
            print("The database is sound.")
            return 0
        print(f"Damaged ({verdict['kind']}, {verdict['problem_count']} problems); repair: {repair_for(verdict)}")
        for line in verdict["problems"]:
            print(f"  {line}")
        return 1
    if _service_active():
        print(f"Stop the service first: systemctl stop {SERVICE}", file=sys.stderr)
        return 3
    if verdict["ok"]:
        print("The database is sound: nothing to rebuild.")
        return 0
    try:
        result = rebuild(args.db, drop_history=args.drop_history)
    except (RebuildFailed, sqlite3.DatabaseError) as e:
        print(f"Not rebuilt: {e}. The database is unchanged.", file=sys.stderr)
        if not args.drop_history:
            print(
                f"A damaged history table ({', '.join(HISTORY_TABLES)}) can be emptied with --drop-history.",
                file=sys.stderr,
            )
        print(
            "A damaged configuration table cannot be rebuilt: see docs/database-health.md.",
            file=sys.stderr,
        )
        return 4
    print(f"Rebuilt: {sum(result['rows'].values())} rows in {len(result['rows'])} tables.")
    if result["emptied"]:
        print(f"Emptied, their history lost: {', '.join(result['emptied'])}.")
    print(f"The damaged file is kept as {result['kept']}. Start the service: systemctl start {SERVICE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
