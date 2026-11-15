"""Availability of VMs and nodes over time (docs/design/console-2.md, section 1).

The metrics collector reads every VM of this node and of each online remote node at each round; it hands their libvirt
state here, with each node's reachability (`observe_round`). Only changes are written: `availability_state` keeps each
target's current state and when it was last seen, `availability_events` one row per period spent in one state. A gap
longer than GAP_S between two observations (this node was down, Hyperlite was stopped) becomes an "unknown" period:
nothing is claimed about a time nobody watched.

Targets follow the metrics' names: "vm" for a VM of this node, "node:vm" for one of a remote node, "host" for this node,
"node:<name>" for a remote node. Both tables are this node's own history, like the metrics.
"""

import logging
from datetime import UTC, datetime, timedelta

from app.core.database import get_conn

logger = logging.getLogger(__name__)

GAP_S = 60  # four rounds of 15 s: beyond, the time between two observations is unknown
RETENTION_DAYS = 400
UP = "running"
PLANNED = {"stopped", "paused"}  # a stop made on purpose: not counted against availability
UNPLANNED = {"crashed", "unreachable", "unknown"}

# libvirt's states and reasons (virDomainState and its *Reason enums), by value so this module reads without libvirt.
_STATE = {1: "running", 2: "running", 3: "paused", 4: "running", 5: "stopped", 6: "crashed", 7: "paused"}
_SHUTOFF = {0: "", 1: "shutdown", 2: "destroyed", 3: "crashed", 4: "migrated", 5: "saved", 6: "failed",
            7: "snapshot", 8: "daemon"}  # fmt: skip
_RUNNING = {0: "", 1: "booted", 2: "migrated", 3: "restored", 4: "snapshot", 5: "unpaused", 6: "migration-cancelled",
            7: "save-cancelled", 8: "wakeup", 9: "crashed", 10: "postcopy"}  # fmt: skip
_PAUSED = {1: "user", 2: "migration", 3: "save", 4: "dump", 5: "io-error", 6: "watchdog", 7: "snapshot",
           8: "shutting-down", 9: "snapshot", 10: "crashed", 11: "starting-up", 12: "postcopy",
           13: "postcopy-failed"}  # fmt: skip


def from_libvirt(state, reason):
    """(state, reason) of ours for a libvirt (state, reason)."""
    ours = _STATE.get(state, "unknown")
    if state == 5:
        why = _SHUTOFF.get(reason, "")
        if why in ("crashed", "failed"):
            return "crashed", why
        return "stopped", why
    if state == 6:
        return "crashed", "panicked" if reason == 1 else "crashed"
    if state in (3, 7):
        why = _PAUSED.get(reason, "")
        # A guest paused by an I/O error or a watchdog is not a pause anybody asked for.
        return ("crashed", why) if why in ("io-error", "watchdog", "crashed", "postcopy-failed") else ("paused", why)
    return ours, _RUNNING.get(reason, "") if ours == "running" else ""


def _now():
    return datetime.now(UTC)


def observe_round(observations, now=None):
    """Record one round: `observations` is [(target, state, reason)]. Writes only what changed, in one transaction."""
    now = now or _now()
    stamp = now.isoformat()
    with get_conn() as db:
        current = {
            r["target"]: dict(r)
            for r in db.execute("SELECT target, state, reason, since, last_seen FROM availability_state")
        }
        seen = []
        newly_unreachable = []
        for target, state, reason in observations:
            reason = reason or ""
            cur = current.get(target)
            if cur is None:
                db.execute(
                    "INSERT INTO availability_state (target, state, reason, since, last_seen) VALUES (?, ?, ?, ?, ?)",
                    (target, state, reason, stamp, stamp),
                )
                _open(db, target, state, reason, stamp)
                continue
            gap = (now - datetime.fromisoformat(cur["last_seen"])).total_seconds() > GAP_S
            if gap:
                _close(db, target, cur["last_seen"])
                db.execute(
                    "INSERT INTO availability_events (target, state, reason, started_at, ended_at) "
                    "VALUES (?, 'unknown', 'not watched', ?, ?)",
                    (target, cur["last_seen"], stamp),
                )
            if target.startswith("node:") and state == "unreachable" and cur["state"] != "unreachable":
                newly_unreachable.append(target[5:])
            if gap or state != cur["state"] or (state != UP and reason != (cur["reason"] or "")):
                if not gap:
                    _close(db, target, stamp)
                _open(db, target, state, reason, stamp)
                db.execute(
                    "UPDATE availability_state SET state = ?, reason = ?, since = ?, last_seen = ? WHERE target = ?",
                    (state, reason, stamp, stamp, target),
                )
            else:
                seen.append(target)
        if seen:
            db.executemany("UPDATE availability_state SET last_seen = ? WHERE target = ?", [(stamp, t) for t in seen])
        db.commit()
    if newly_unreachable:
        from app.core.audit import log_action

        for name in newly_unreachable:
            log_action("system", "node_unreachable", name, "failed", "the node stopped answering")


def _open(db, target, state, reason, at):
    db.execute(
        "INSERT INTO availability_events (target, state, reason, started_at) VALUES (?, ?, ?, ?)",
        (target, state, reason, at),
    )


def _close(db, target, at):
    db.execute("UPDATE availability_events SET ended_at = ? WHERE target = ? AND ended_at IS NULL", (at, target))


def prune(now=None):
    """Forget events older than RETENTION_DAYS and targets not seen since (hourly, with the metrics' roll-up)."""
    limit = ((now or _now()) - timedelta(days=RETENTION_DAYS)).isoformat()
    with get_conn() as db:
        db.execute("DELETE FROM availability_events WHERE ended_at IS NOT NULL AND ended_at < ?", (limit,))
        db.execute("DELETE FROM availability_state WHERE last_seen < ?", (limit,))
        db.commit()


def target_of(kind, name, node=None):
    """The metrics' name of a VM ("vm", "node:vm") or a node ("host", "node:<name>")."""
    from app.core import self_node

    local = not node or node == "local" or node == self_node.name()
    if kind == "node":
        return "host" if name in ("local", self_node.name()) else f"node:{name}"
    return name if local else f"{node}:{name}"


def current(target, now=None):
    """{state, reason, since} now: "unknown" when the target was not seen within GAP_S."""
    with get_conn() as db:
        row = db.execute("SELECT * FROM availability_state WHERE target = ?", (target,)).fetchone()
    if row is None:
        return {"state": "unknown", "reason": "never seen", "since": None}
    if ((now or _now()) - datetime.fromisoformat(row["last_seen"])).total_seconds() > GAP_S:
        return {"state": "unknown", "reason": "not seen lately", "since": row["last_seen"]}
    return {"state": row["state"], "reason": row["reason"] or "", "since": row["since"]}


def _periods(target, start, end):
    """The periods of `target` overlapping [start, end], clipped to it, oldest first."""
    with get_conn() as db:
        rows = db.execute(
            "SELECT state, reason, started_at, ended_at FROM availability_events WHERE target = ? "
            "AND started_at < ? AND (ended_at IS NULL OR ended_at > ?) ORDER BY started_at, id",
            (target, end.isoformat(), start.isoformat()),
        ).fetchall()
        last_seen = db.execute("SELECT last_seen FROM availability_state WHERE target = ?", (target,)).fetchone()
    out = []
    for r in rows:
        a = max(datetime.fromisoformat(r["started_at"]), start)
        b = min(datetime.fromisoformat(r["ended_at"]) if r["ended_at"] else end, end)
        if r["ended_at"] is None and last_seen is not None:
            # The open period stops where the target was last seen; the rest is unknown, as at the next round.
            seen = datetime.fromisoformat(last_seen["last_seen"])
            if (end - seen).total_seconds() > GAP_S:
                b = min(b, seen)
                if b > a:
                    out.append({"state": r["state"], "reason": r["reason"] or "", "start": a, "end": b})
                out.append({"state": "unknown", "reason": "not seen lately", "start": max(seen, start), "end": end})
                continue
        if b > a:
            out.append({"state": r["state"], "reason": r["reason"] or "", "start": a, "end": b})
    return out


def measure(target, days=7, now=None):
    """Running, planned and unplanned time over the last `days`, the share available, and the periods."""
    end = now or _now()
    start = end - timedelta(days=days)
    periods = _periods(target, start, end)
    totals = {"running": 0.0, "planned": 0.0, "unplanned": 0.0}
    previous = None
    for p in periods:
        seconds = (p["end"] - p["start"]).total_seconds()
        kind = _kind(p, previous)
        p["kind"] = kind
        totals[kind] += seconds
        previous = p
    watched = sum(totals.values())
    meant = totals["running"] + totals["unplanned"]
    return {
        "days": days,
        "from": (periods[0]["start"] if periods else start).isoformat(),
        "running_s": round(totals["running"]),
        "planned_s": round(totals["planned"]),
        "unplanned_s": round(totals["unplanned"]),
        "uptime_pct": round(totals["running"] / watched * 100, 3) if watched else None,
        "available_pct": round(totals["running"] / meant * 100, 3) if meant else None,
        "interruptions": sum(1 for p in periods if p["kind"] == "unplanned"),
        "periods": periods,
    }


def _kind(period, previous):
    if period["state"] == UP:
        return "running"
    if period["state"] == "unknown":
        # Unknown after a stop made on purpose stays planned: a VM switched off is not "down" while nobody watched.
        return "planned" if previous is not None and previous.get("kind") == "planned" else "unplanned"
    return "planned" if period["state"] in PLANNED else "unplanned"


ACTIONS = {
    "start_vm": "started",
    "stop_vm": "stopped",
    "shutdown_vm": "stopped",
    "force_stop_vm": "forced off",
    "restart_vm": "restarted",
    "reboot_vm": "restarted",
    "migrate_vm": "migrated",
    "vm_schedule": "schedule",
    "ha_recover": "HA",
    "guest_updates": "guest updates",
}


def who(name, at, window_s=90):
    """The journal entry that explains a change of `name` at `at`: {username, action}, or None."""
    lo, hi = (at - timedelta(seconds=window_s)).isoformat(), (at + timedelta(seconds=window_s)).isoformat()
    marks = ",".join("?" for _ in ACTIONS)
    with get_conn() as db:
        row = db.execute(
            f"SELECT username, action, timestamp FROM audit_log WHERE resource = ? AND action IN ({marks}) "  # noqa: S608 (placeholders only)
            "AND timestamp BETWEEN ? AND ? ORDER BY ABS(julianday(timestamp) - julianday(?)) LIMIT 1",
            (name, *ACTIONS, lo, hi, at.isoformat()),
        ).fetchone()
    return {"username": row["username"], "action": row["action"]} if row else None


def report(kind, name, node=None, days=7, now=None):
    """The Availability tab: the state now, the measures over 1, 7 and 30 days (or `days`), and the interruptions."""
    target = target_of(kind, name, node)
    now = now or _now()
    windows = sorted({1, 7, 30, days})
    measures = {d: measure(target, d, now) for d in windows}
    main = measures[days]
    events = []
    previous = None
    for p in main["periods"]:
        if p["state"] != UP:
            entry = {
                "state": p["state"],
                "reason": p["reason"],
                "kind": p["kind"],
                "start": p["start"].isoformat(),
                "end": p["end"].isoformat(),
                "seconds": round((p["end"] - p["start"]).total_seconds()),
            }
            if kind == "vm" and previous is not None:
                entry["by"] = who(name, p["start"])
            events.append(entry)
        previous = p
    strip = [
        {"state": p["state"], "kind": p["kind"], "start": p["start"].isoformat(), "end": p["end"].isoformat()}
        for p in main["periods"]
    ]
    return {
        "target": target,
        "now": current(target, now),
        "windows": {str(d): {k: v for k, v in m.items() if k != "periods"} for d, m in measures.items()},
        "strip": strip,
        "events": list(reversed(events))[:200],
    }


def summary(days=7, now=None):
    """Every target this node watches: its state now and its share available over `days`."""
    now = now or _now()
    with get_conn() as db:
        targets = [r["target"] for r in db.execute("SELECT target FROM availability_state ORDER BY target")]
    out = []
    for target in targets:
        m = measure(target, days, now)
        out.append(
            {
                "target": target,
                "now": current(target, now),
                "available_pct": m["available_pct"],
                "interruptions": m["interruptions"],
            }
        )
    return out
