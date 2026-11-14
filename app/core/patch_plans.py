"""Patch plans (docs/design/console-2.md, section 4): recurring windows for guest updates.

A plan names its VMs (a list, a pool or a tag), its days of the week or a day of the month, a time in a time zone, a
length and whether to reboot. At each occurrence `tick` (every minute, the access sweep's loop) resolves the running VMs
of this node at that moment and schedules a run with the guest updates' own engine (app/core/guest_updates.py), so
following, rebooting and the outcome are those of a run made by hand. A plan acts with its author's rights: a VM the
author may no longer run commands in (vm.exec) is left out, and the plan stops when its author is gone.
"""

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.database import get_conn

logger = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
KINDS = ("vms", "pool", "tag")


class PlanError(ValueError):
    pass


def _now():
    return datetime.now(UTC)


def _zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise PlanError(f"Unknown time zone: {name}") from None


def validate(f):
    name = (f.get("name") or "").strip()
    if not 1 <= len(name) <= 100:
        raise PlanError("A name of 1 to 100 characters")
    kind = f.get("target_kind")
    if kind not in KINDS:
        raise PlanError("A plan holds on a list of VMs, a pool or a tag")
    target = f.get("target")
    if kind == "vms":
        if not isinstance(target, list) or not target or len(target) > 200:
            raise PlanError("Name 1 to 200 VMs")
        target = sorted({str(v) for v in target})
    elif kind == "pool":
        if not str(target or "").isdigit():
            raise PlanError("Name the pool by its id")
        target = str(target)
    else:
        from app.core import object_meta

        tags = object_meta.normalize_tags([target])
        if not tags:
            raise PlanError("Name the tag")
        target = tags[0]
    days = sorted({int(d) for d in (f.get("days") or [])})
    monthday = f.get("monthday")
    if bool(days) == bool(monthday):
        raise PlanError("Choose days of the week, or a day of the month")
    if days and not all(0 <= d <= 6 for d in days):
        raise PlanError("Days of the week go from 0 (Monday) to 6 (Sunday)")
    if monthday is not None and not (1 <= int(monthday) <= 28 or int(monthday) == -1):
        raise PlanError("A day of the month from 1 to 28, or -1 for the last one")
    at = f.get("at_time") or ""
    if not TIME_RE.match(at):
        raise PlanError("A time as HH:MM")
    tz = f.get("tz") or "UTC"
    _zone(tz)
    duration = int(f.get("duration_min") or 0)
    if not 30 <= duration <= 1440:
        raise PlanError("A window of 30 minutes to 24 hours")
    return {
        "name": name,
        "target_kind": kind,
        "target": target,
        "days": days,
        "monthday": int(monthday) if monthday else None,
        "at_time": at,
        "tz": tz,
        "duration_min": duration,
        "reboot": bool(f.get("reboot")),
        "enabled": bool(f.get("enabled", True)),
    }


def _row(r):
    p = dict(r)
    p["days"] = json.loads(p["days"] or "[]")
    p["target"] = json.loads(p["target"])
    p["reboot"] = bool(p["reboot"])
    p["enabled"] = bool(p["enabled"])
    return p


def plans():
    with get_conn() as db:
        rows = [_row(r) for r in db.execute("SELECT * FROM guest_update_plans ORDER BY name")]
    now = _now()
    for p in rows:
        p["next"] = [o.isoformat() for o in occurrences(p, now, 3)]
    return rows


def get(plan_id):
    with get_conn() as db:
        row = db.execute("SELECT * FROM guest_update_plans WHERE id = ?", (plan_id,)).fetchone()
    return _row(row) if row else None


def save(fields, username, plan_id=None):
    f = validate(fields)
    now = _now()
    # The latest occurrence already past counts as done: a plan made at 10:00 for "every day at 03:00" waits for
    # tomorrow instead of starting at once.
    last = latest(f, now)
    values = (
        f["name"],
        f["target_kind"],
        json.dumps(f["target"]),
        json.dumps(f["days"]),
        f["monthday"],
        f["at_time"],
        f["tz"],
        f["duration_min"],
        1 if f["reboot"] else 0,
        1 if f["enabled"] else 0,
        last.isoformat() if last else None,
    )
    with get_conn() as db:
        if plan_id is None:
            plan_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO guest_update_plans (name, target_kind, target, days, monthday, at_time, tz, duration_min, "
                "reboot, enabled, last_occurrence, id, created_by, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, plan_id, username, now.isoformat()),
            )
        else:
            cur = db.execute(
                "UPDATE guest_update_plans SET name = ?, target_kind = ?, target = ?, days = ?, monthday = ?, "
                "at_time = ?, tz = ?, duration_min = ?, reboot = ?, enabled = ?, last_occurrence = ? WHERE id = ?",
                (*values, plan_id),
            )
            if cur.rowcount == 0:
                return None
        db.commit()
    return next(p for p in plans() if p["id"] == plan_id)


def delete(plan_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM guest_update_plans WHERE id = ?", (plan_id,))
        db.commit()
    return cur.rowcount > 0


def _on_day(plan, day):
    if plan["days"]:
        return day.weekday() in plan["days"]
    if plan["monthday"] == -1:
        return (day + timedelta(days=1)).month != day.month
    return day.day == plan["monthday"]


def _at(plan, day, zone):
    hh, mm = (int(x) for x in plan["at_time"].split(":"))
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC)


def occurrences(plan, after, n):
    """The next `n` starts strictly after `after`, in UTC."""
    zone = _zone(plan["tz"])
    day = after.astimezone(zone).date()
    out = []
    for _ in range(400):
        if _on_day(plan, day):
            at = _at(plan, day, zone)
            if at > after:
                out.append(at)
                if len(out) == n:
                    break
        day += timedelta(days=1)
    return out


def latest(plan, now):
    """The latest start at or before `now`, within the last 32 days, in UTC; None when there is none."""
    zone = _zone(plan["tz"])
    day = now.astimezone(zone).date()
    for _ in range(33):
        if _on_day(plan, day):
            at = _at(plan, day, zone)
            if at <= now:
                return at
        day -= timedelta(days=1)
    return None


def _local_running():
    from app.core.libvirt_utils import open_conn

    conn = open_conn(readonly=True)
    try:
        return {d.name() for d in conn.listAllDomains() if d.isActive()}
    finally:
        conn.close()


def resolve(plan, running=None):
    """The running VMs of this node the plan holds on now."""
    running = _local_running() if running is None else running
    if plan["target_kind"] == "vms":
        wanted = set(plan["target"])
    elif plan["target_kind"] == "pool":
        from app.core import permissions

        pool = next((p for p in permissions.list_pools() if str(p["id"]) == plan["target"]), None)
        wanted = set(pool["vms"]) | set(pool.get("dynamic_vms") or []) if pool else set()
    else:
        from app.core import object_meta

        wanted = {vm for vm in running if plan["target"] in object_meta.effective_tags("vm", vm)}
    return sorted(wanted & running)


def tick(now=None):
    """Schedule the runs of the plans whose occurrence came (every minute)."""
    from app.core import guest_updates, permissions
    from app.core.audit import log_action
    from app.services import account_service

    now = now or _now()
    for plan in plans():
        if not plan["enabled"]:
            continue
        occ = latest(plan, now)
        if occ is None or (plan["last_occurrence"] and datetime.fromisoformat(plan["last_occurrence"]) >= occ):
            continue
        end = occ + timedelta(minutes=plan["duration_min"])
        with get_conn() as db:
            db.execute("UPDATE guest_update_plans SET last_occurrence = ? WHERE id = ?", (occ.isoformat(), plan["id"]))
            db.commit()
        if now >= end:
            log_action("system", "patch_plan", plan["name"], "failed", f"window of {occ.isoformat()} missed")
            continue
        author = account_service.get(plan["created_by"]) if plan["created_by"] else None
        if author is None:
            log_action("system", "patch_plan", plan["name"], "failed", "its author no longer exists: plan skipped")
            continue
        vms = [vm for vm in resolve(plan) if permissions.has_privilege(author, vm, "vm.exec")]
        if not vms:
            log_action("system", "patch_plan", plan["name"], "success", "no running VM to update")
            continue
        run = guest_updates.schedule(vms, occ.isoformat(), end.isoformat(), plan["reboot"], f"plan:{plan['name']}")
        with get_conn() as db:
            db.execute("UPDATE guest_update_plans SET last_run = ? WHERE id = ?", (run["id"], plan["id"]))
            db.commit()
        log_action("system", "patch_plan", plan["name"], "success", f"run {run['id']} on {', '.join(vms)}")
