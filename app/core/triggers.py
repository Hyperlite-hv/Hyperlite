"""Triggers (docs/design/console-2.md, section 6): an automation job runs by itself when something happens.

The events are read where they are already journaled (app/core/audit.py hands each matching entry here, off the
request, as it does for the notification centre): an alert rule firing (not while silenced), a VM crashing, a backup
failing, a node that stops answering, a guest update run failing. A trigger names the event, an optional filter (the
rule's name, the VM, the node), the job, whether the VM concerned becomes the job's target (its "each target" steps),
and a cool-down. Each firing is kept with the run it started, or why none started.
"""

import logging
import re
import threading
import uuid
from datetime import UTC, datetime, timedelta

from app.core.database import get_conn

logger = logging.getLogger(__name__)

EVENTS = ("alert_fired", "vm_crashed", "backup_failed", "node_unreachable", "guest_update_failed")
# Journal action -> event; only these entries are looked at.
ACTIONS = {
    "alert_rule_fired": "alert_fired",
    "vm_crashed": "vm_crashed",
    "backup_vm": "backup_failed",
    "node_unreachable": "node_unreachable",
    "guest_updates": "guest_update_failed",
}
_TARGET_RE = re.compile(r"^(?P<vm>[^ ]+) \((?P<node>[^)]+)\)$")
_lock = threading.Lock()


class TriggerError(ValueError):
    pass


def _now():
    return datetime.now(UTC)


def _row(r):
    t = dict(r)
    t["pass_target"] = bool(t["pass_target"])
    t["enabled"] = bool(t["enabled"])
    return t


def triggers():
    with get_conn() as db:
        rows = [_row(r) for r in db.execute("SELECT * FROM job_triggers ORDER BY name")]
        last = {
            r["trigger_id"]: dict(r)
            for r in db.execute(
                "SELECT f.* FROM trigger_firings f JOIN (SELECT trigger_id, MAX(at) AS at FROM trigger_firings "
                "GROUP BY trigger_id) m ON f.trigger_id = m.trigger_id AND f.at = m.at"
            )
        }
    for t in rows:
        t["last_firing"] = last.get(t["id"])
    return rows


def firings(trigger_id=None, limit=100):
    with get_conn() as db:
        if trigger_id:
            rows = db.execute(
                "SELECT * FROM trigger_firings WHERE trigger_id = ? ORDER BY at DESC LIMIT ?", (trigger_id, limit)
            )
        else:
            rows = db.execute("SELECT * FROM trigger_firings ORDER BY at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def validate(f):
    name = (f.get("name") or "").strip()
    if not 1 <= len(name) <= 100:
        raise TriggerError("A name of 1 to 100 characters")
    if f.get("event") not in EVENTS:
        raise TriggerError(f"An event among: {', '.join(EVENTS)}")
    flt = (f.get("filter") or "").strip() or None
    if flt and len(flt) > 200:
        raise TriggerError("A filter of at most 200 characters")
    from app.core import jobs

    try:
        job_id = int(f.get("job_id") or 0)
    except (TypeError, ValueError):
        raise TriggerError("No such job") from None
    if not jobs._store().get_job(job_id):
        raise TriggerError("No such job")
    cooldown = int(f.get("cooldown_min", 30))
    if not 1 <= cooldown <= 10080:
        raise TriggerError("A cool-down of 1 minute to 7 days")
    return {
        "name": name,
        "event": f["event"],
        "filter": flt,
        "job_id": int(f["job_id"]),
        "pass_target": bool(f.get("pass_target", True)),
        "cooldown_min": cooldown,
        "enabled": bool(f.get("enabled", True)),
    }


def save(fields, username, trigger_id=None):
    f = validate(fields)
    values = (f["name"], f["event"], f["filter"], f["job_id"], 1 if f["pass_target"] else 0, f["cooldown_min"],
              1 if f["enabled"] else 0)  # fmt: skip
    with get_conn() as db:
        if trigger_id is None:
            trigger_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO job_triggers (name, event, filter, job_id, pass_target, cooldown_min, enabled, id, "
                "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, trigger_id, username, _now().isoformat()),
            )
        else:
            cur = db.execute(
                "UPDATE job_triggers SET name = ?, event = ?, filter = ?, job_id = ?, pass_target = ?, "
                "cooldown_min = ?, enabled = ? WHERE id = ?",
                (*values, trigger_id),
            )
            if cur.rowcount == 0:
                return None
        db.commit()
    return next(t for t in triggers() if t["id"] == trigger_id)


def delete(trigger_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM job_triggers WHERE id = ?", (trigger_id,))
        db.execute("DELETE FROM trigger_firings WHERE trigger_id = ?", (trigger_id,))
        db.commit()
    return cur.rowcount > 0


def event_of(action, resource, result, detail):
    """(event, subjects, label) for a journal entry, or None. `subjects`: the VMs (or the node) concerned; `label`:
    what a filter is matched against besides them (the alert rule's name)."""
    event = ACTIONS.get(action)
    if event is None:
        return None
    detail = detail or ""
    if event == "alert_fired":
        if detail.endswith("(silenced)"):
            return None
        rule = detail.split(":", 1)[0].strip()
        m = _TARGET_RE.match(resource or "")
        return event, [m.group("vm")] if m else [resource], rule
    if event in ("backup_failed", "guest_update_failed") and result == "success":
        return None
    if event == "guest_update_failed":
        failed = detail.removeprefix("failed on ").split(", ") if detail.startswith("failed on ") else []
        return event, [v for v in failed if v], None
    return event, [resource], None


def _matches(trigger, subjects, label):
    flt = trigger["filter"]
    return not flt or flt in subjects or (label is not None and flt == label)


def on_journal(action, resource, result, detail, now=None):
    """Fire the triggers matching a journal entry (called by app/core/audit.py on its own thread)."""
    found = event_of(action, resource, result, detail)
    if found is None:
        return []
    event, subjects, label = found
    fired = []
    with _lock:  # two entries at once must not both pass the same cool-down
        for t in triggers():
            if t["enabled"] and t["event"] == event and _matches(t, subjects, label):
                fired.append(fire(t, subjects, now or _now()))
    return fired


def fire(trigger, subjects, now):
    """Start the trigger's job for `subjects`, unless within its cool-down; the firing row."""
    from app.core.audit import log_action
    from app.core.jobs import JobRunRefused, start_job_run

    subject = ", ".join(s for s in subjects if s) or "-"
    if trigger["last_fired_at"] and now - datetime.fromisoformat(trigger["last_fired_at"]) < timedelta(
        minutes=trigger["cooldown_min"]
    ):
        return _firing(trigger, now, subject, None, "skipped: cool-down")
    run_id, outcome = None, "started"
    targets = [s for s in subjects if s] if trigger["pass_target"] and trigger["event"] != "node_unreachable" else []
    try:
        run_id = start_job_run(trigger["job_id"], targets, False, f"trigger:{trigger['name']}")
    except (LookupError, JobRunRefused) as e:
        outcome = f"refused: {e}"
    with get_conn() as db:
        db.execute("UPDATE job_triggers SET last_fired_at = ? WHERE id = ?", (now.isoformat(), trigger["id"]))
        db.commit()
    log_action("system", "trigger_fired", trigger["name"], "success" if run_id else "failed", f"{subject}: {outcome}")
    return _firing(trigger, now, subject, run_id, outcome)


def _firing(trigger, now, subject, run_id, outcome):
    row = {"id": uuid.uuid4().hex, "trigger_id": trigger["id"], "at": now.isoformat(), "subject": subject,
           "run_id": run_id, "outcome": outcome}  # fmt: skip
    with get_conn() as db:
        db.execute(
            "INSERT INTO trigger_firings (id, trigger_id, at, subject, run_id, outcome) VALUES (?, ?, ?, ?, ?, ?)",
            tuple(row.values()),
        )
        db.execute(
            "DELETE FROM trigger_firings WHERE trigger_id = ? AND id NOT IN (SELECT id FROM trigger_firings "
            "WHERE trigger_id = ? ORDER BY at DESC LIMIT 200)",
            (trigger["id"], trigger["id"]),
        )
        db.commit()
    return row
