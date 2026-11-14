"""The notification centre (docs/design/updates-2.md, 2.4): what happened that someone should see, kept on this node,
read and archived per account.

An entry has a level (info, update, warning, error), a kind and its subject (the interface words it in the reader's
language from those; `title` is the English sentence for anything else that reads it), a detail, a link to a page of
the console ("type:id:tab", "datacenter::updates"), an audience (everyone, or administrators only) and, for what
lasts, a resolve_key: one open entry per key, pinned on top until resolve() says it is over.

Fed by the journal (from_journal, for the actions that already notify the channels), by the update runs and by the
version check.
"""

import logging
from datetime import UTC, datetime

from app.core.database import get_conn

logger = logging.getLogger(__name__)

LEVELS = ("info", "update", "warning", "error")
KEPT = 500  # entries kept on the node, the newest


def _now():
    return datetime.now(UTC).isoformat()


def add(level, kind, title, *, subject=None, detail=None, link=None, audience="all", resolve_key=None):
    """A new entry, or None when an open entry with the same resolve_key already says it."""
    if level not in LEVELS:
        raise ValueError(f"Unknown level: {level}")
    with get_conn() as db:
        if (
            resolve_key
            and db.execute(
                "SELECT 1 FROM notification_feed WHERE resolve_key = ? AND resolved_at IS NULL", (resolve_key,)
            ).fetchone()
        ):
            return None
        cur = db.execute(
            "INSERT INTO notification_feed (created_at, level, kind, subject, title, detail, link, audience, resolve_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), level, kind, subject, title[:300], (detail or "")[:1000] or None, link, audience, resolve_key),
        )
        db.execute(
            "DELETE FROM notification_feed WHERE id NOT IN (SELECT id FROM notification_feed ORDER BY id DESC LIMIT ?)",
            (KEPT,),
        )
        db.execute("DELETE FROM notification_marks WHERE notification_id NOT IN (SELECT id FROM notification_feed)")
        db.commit()
        return cur.lastrowid


def resolve(resolve_key):
    """What the open entry of this key reported is over: it is no longer pinned."""
    with get_conn() as db:
        db.execute(
            "UPDATE notification_feed SET resolved_at = ? WHERE resolve_key = ? AND resolved_at IS NULL",
            (_now(), resolve_key),
        )
        db.commit()


# An administrator reads every entry, anyone else the entries meant for everyone: `? OR audience = 'all'`.
_SELECT = (
    "SELECT f.*, m.read_at, m.archived_at FROM notification_feed f "
    "LEFT JOIN notification_marks m ON m.notification_id = f.id AND m.username = ? "
    "WHERE (? OR f.audience = 'all') AND (m.archived_at IS NOT NULL) = ? "
    "ORDER BY (f.resolve_key IS NOT NULL AND f.resolved_at IS NULL) DESC, f.id DESC LIMIT ?"
)
_UNREAD = (
    "SELECT COUNT(*) AS n FROM notification_feed f "
    "LEFT JOIN notification_marks m ON m.notification_id = f.id AND m.username = ? "
    "WHERE (? OR f.audience = 'all') AND m.read_at IS NULL AND m.archived_at IS NULL"
)
_MARKS = {
    "read_at": "INSERT INTO notification_marks (username, notification_id, read_at) VALUES (?, ?, ?) "
    "ON CONFLICT(username, notification_id) DO UPDATE SET read_at = COALESCE(read_at, excluded.read_at)",
    "archived_at": "INSERT INTO notification_marks (username, notification_id, archived_at) VALUES (?, ?, ?) "
    "ON CONFLICT(username, notification_id) DO UPDATE SET archived_at = COALESCE(archived_at, excluded.archived_at)",
}


def entries(username, admin, archived=False, limit=50):
    """The entries this account may read, newest first, pinned ones (open, with a resolve_key) on top; each with its
    read and archived marks."""
    with get_conn() as db:
        rows = db.execute(_SELECT, (username, 1 if admin else 0, 1 if archived else 0, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["read"] = d.pop("read_at") is not None
        d["archived"] = d.pop("archived_at") is not None
        d["pinned"] = bool(d["resolve_key"]) and d["resolved_at"] is None
        out.append(d)
    return out


def unread_count(username, admin):
    with get_conn() as db:
        return db.execute(_UNREAD, (username, 1 if admin else 0)).fetchone()["n"]


def _mark(username, admin, ids, column):
    now = _now()
    with get_conn() as db:
        if ids is None:
            ids = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM notification_feed WHERE (? OR audience = 'all')", (1 if admin else 0,)
                ).fetchall()
            ]
        for nid in ids:
            db.execute(_MARKS[column], (username, int(nid), now))
        db.commit()


def mark_read(username, admin, ids=None):
    """These entries (all when None) read by this account."""
    _mark(username, admin, ids, "read_at")


def archive(username, admin, ids):
    """These entries out of this account's list (read too); a pinned one stays pinned for the others."""
    _mark(username, admin, ids, "read_at")
    _mark(username, admin, ids, "archived_at")


# From the journal: the actions that already notify the channels, as the centre words them.
def from_journal(action, resource, result, detail):
    try:
        _from_journal(action, resource, result, detail)
    except Exception:
        logger.warning("Journal entry %s not added to the notification centre", action, exc_info=True)


def _from_journal(action, resource, result, detail):
    if action == "vm_crashed":
        add(
            "error",
            "vm_crashed",
            f"VM {resource} crashed",
            subject=resource,
            detail=detail,
            link=f"vm:{resource}:summary",
        )
    elif action == "host_reboot_required":
        add("warning", "reboot_required", "The node needs a reboot", detail=detail, link="datacenter::updates",
            audience="admin", resolve_key="reboot_required")  # fmt: skip
    elif action == "alert_rule_fired":
        rule = (detail or "").split(":", 1)[0]
        add("warning", "alert_fired", f"Alert {rule}: {resource}", subject=f"{rule} · {resource}", detail=detail,
            link="datacenter::alerts", resolve_key=f"alert:{rule}:{resource}")  # fmt: skip
    elif action == "alert_rule_resolved":
        resolve(f"alert:{(detail or '').split(':', 1)[0]}:{resource}")
    elif action in ("backup_vm", "backup_job_failed") and result != "success":
        add(
            "error",
            "backup_failed",
            f"Backup of {resource} failed",
            subject=resource,
            detail=detail,
            link="datacenter::backups",
        )
    elif action == "ha_alert":
        add(
            "error",
            "ha_alert",
            f"HA: {resource}",
            subject=resource,
            detail=detail,
            link="datacenter::ha",
            audience="admin",
        )
    elif action == "node_status_change":
        add(
            "warning" if result != "success" else "info",
            "node_state",
            f"Node {resource}",
            subject=resource,
            detail=detail,
            link=f"node:{resource}:summary",
        )
    elif action == "update_check_blocked":
        add("warning", "update_source", "The update check cannot reach the repository", detail=detail,
            link="datacenter::updates", audience="admin", resolve_key="update_source")  # fmt: skip


# From the updates (app/core/update_runs.py, app/core/update_check.py).
def update_available(version, critical, detail=None):
    resolve("update_source")
    add("error" if critical else "update", "update_critical" if critical else "update_available",
        f"Hyperlite {version} is available" + (" (security)" if critical else ""), subject=version, detail=detail,
        link="datacenter::updates", audience="admin", resolve_key=f"update_available:{version}")  # fmt: skip


def up_to_date():
    """No update is waiting any more: the "available" entries are over."""
    with get_conn() as db:
        keys = [r["resolve_key"] for r in db.execute(
            "SELECT resolve_key FROM notification_feed WHERE resolve_key LIKE 'update_available:%' AND resolved_at IS NULL"
        ).fetchall()]  # fmt: skip
    for key in keys:
        resolve(key)


def update_outcome(run):
    status, target = run["status"], run.get("to_version") or ""
    link = "datacenter::updates"
    if status == "succeeded":
        up_to_date()
        add("info", "update_done", f"Update to {target} done", subject=target, link=link, audience="admin")
        if run.get("reboot_required"):
            add(
                "warning",
                "reboot_required",
                "The node needs a reboot",
                link=link,
                audience="admin",
                resolve_key="reboot_required",
            )
    elif status == "rolled_back":
        add(
            "error",
            "update_rolled_back",
            f"Update to {target} rolled back",
            subject=target,
            detail=run.get("error"),
            link=link,
            audience="admin",
        )
    elif status == "failed":
        add(
            "error",
            "update_failed",
            f"Update to {target} failed",
            subject=target,
            detail=run.get("error"),
            link=link,
            audience="admin",
        )


def update_started(run):
    add("info", "update_started", f"Update to {run.get('to_version') or 'the new version'} started",
        subject=run.get("to_version"), link="datacenter::updates", audience="admin")  # fmt: skip
