"""Autoscaling of VM groups (docs/design/console-2.md, section 5).

A group may follow its load: a minimum and a maximum count, a CPU threshold to add VMs and one to remove one, a window,
a step and a cool-down. Every minute (the access sweep's loop) `tick` reads the average CPU of the group's running
members over the window from the metrics' raw tier:

- above the high threshold over the whole window, `step` VMs are deployed from the template (app/core/vm_groups.py)
  and started once their copy is done;
- below the low threshold, the highest-numbered member is shut down gracefully, then deleted (to the recycle bin when it
  is on) once stopped, or after DRAIN_MIN minutes;
- never beyond the bounds, never twice within the cool-down, and nothing while a previous decision is still under way.

Each decision is journaled and kept with its reason on the group.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from app.core.database import get_conn

logger = logging.getLogger(__name__)

SYSTEM = {"username": "system", "role": "admin"}
DRAIN_MIN = 5
START_GIVE_UP_MIN = 30
COVER = 0.8  # the samples must cover this share of the window before a decision


class ScalingError(ValueError):
    pass


def _now():
    return datetime.now(UTC)


def validate(f):
    out = {
        "enabled": bool(f.get("enabled", True)),
        "min_count": int(f.get("min_count", 1)),
        "max_count": int(f.get("max_count", 3)),
        "up_pct": float(f.get("up_pct", 75)),
        "down_pct": float(f.get("down_pct", 25)),
        "window_min": int(f.get("window_min", 10)),
        "step": int(f.get("step", 1)),
        "cooldown_min": int(f.get("cooldown_min", 15)),
    }
    if not 0 <= out["min_count"] <= out["max_count"] <= 100:
        raise ScalingError("0 ≤ minimum ≤ maximum ≤ 100")
    if not 0 < out["down_pct"] < out["up_pct"] <= 100:
        raise ScalingError("The low threshold must be below the high one, both between 0 and 100 %")
    if out["up_pct"] - out["down_pct"] < 10:
        raise ScalingError("Keep at least 10 points between the thresholds, or the group would go up and down")
    if not 5 <= out["window_min"] <= 120:
        raise ScalingError("A window of 5 to 120 minutes")
    if not 1 <= out["step"] <= 10:
        raise ScalingError("A step of 1 to 10 VMs")
    if not 5 <= out["cooldown_min"] <= 1440:
        raise ScalingError("A cool-down of 5 minutes to 24 hours")
    return out


def _row(r):
    s = dict(r)
    s["enabled"] = bool(s["enabled"])
    s["pending"] = json.loads(s["pending"] or "[]")
    s["history"] = json.loads(s["history"] or "[]")
    return s


def get(group_id):
    with get_conn() as db:
        row = db.execute("SELECT * FROM vm_group_scaling WHERE group_id = ?", (group_id,)).fetchone()
    return _row(row) if row else None


def all_settings():
    with get_conn() as db:
        return {r["group_id"]: _row(r) for r in db.execute("SELECT * FROM vm_group_scaling")}


def save(group_id, fields):
    f = validate(fields)
    with get_conn() as db:
        db.execute(
            "INSERT INTO vm_group_scaling (group_id, enabled, min_count, max_count, up_pct, down_pct, window_min, step, "
            "cooldown_min) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET "
            "enabled = excluded.enabled, min_count = excluded.min_count, max_count = excluded.max_count, "
            "up_pct = excluded.up_pct, down_pct = excluded.down_pct, window_min = excluded.window_min, "
            "step = excluded.step, cooldown_min = excluded.cooldown_min",
            (
                group_id,
                1 if f["enabled"] else 0,
                f["min_count"],
                f["max_count"],
                f["up_pct"],
                f["down_pct"],
                f["window_min"],
                f["step"],
                f["cooldown_min"],
            ),
        )
        db.commit()
    return get(group_id)


def forget(group_id):
    with get_conn() as db:
        db.execute("DELETE FROM vm_group_scaling WHERE group_id = ?", (group_id,))
        db.commit()


def _record(group_id, action, reason, now, pending=None, draining=None):
    s = get(group_id)
    history = ([{"at": now.isoformat(), "action": action, "reason": reason}] + (s["history"] if s else []))[:20]
    with get_conn() as db:
        db.execute(
            "UPDATE vm_group_scaling SET last_action = ?, last_action_at = ?, last_reason = ?, history = ?, "
            "pending = COALESCE(?, pending), draining = ?, draining_since = ? WHERE group_id = ?",
            (
                action,
                now.isoformat(),
                reason,
                json.dumps(history),
                json.dumps(pending) if pending is not None else None,
                draining,
                now.isoformat() if draining else None,
                group_id,
            ),
        )
        db.commit()


def _set(group_id, **cols):
    sets = ", ".join(f"{k} = ?" for k in cols)
    with get_conn() as db:
        db.execute(f"UPDATE vm_group_scaling SET {sets} WHERE group_id = ?", (*cols.values(), group_id))  # noqa: S608 (column names are ours)
        db.commit()


def average_cpu(names, window_min, now, samples=None):
    """The members' average CPU over the window, or None when the samples do not cover it yet."""
    since = now - timedelta(minutes=window_min)
    if samples is None:
        from app.repositories import registry

        samples = registry.metrics().sync.recent_raw(since.isoformat())
    values, first = [], None
    for s in samples:
        if s.get("target") in names and s.get("cpu_pct") is not None:
            ts = datetime.fromisoformat(s["ts"])
            if ts >= since:
                values.append(s["cpu_pct"])
                first = ts if first is None or ts < first else first
    if not values or first is None or (now - first).total_seconds() < window_min * 60 * COVER:
        return None
    return sum(values) / len(values)


def _domains():
    from app.core import vm_groups

    return vm_groups._domains()


def _follow_pending(group, s, domains, now):
    """Start the VMs deployed by an earlier decision once their copy is done."""
    from app.core import privileged, vm_locks
    from app.daemon.registry import DaemonError

    left = []
    for p in s["pending"]:
        name, since = p["name"], datetime.fromisoformat(p["since"])
        if domains.get(name):
            continue  # running: done
        if (now - since).total_seconds() > START_GIVE_UP_MIN * 60:
            _record(group["id"], "start_failed", f"{name} was not ready after {START_GIVE_UP_MIN} min", now)
            continue
        if name in domains and vm_locks.running(name) is None:
            try:
                privileged.run("vm.power", user="system", vm=name, action="start")
                continue
            except DaemonError as e:
                logger.warning("Autoscaling: %s not started: %s", name, e.detail)
        left.append(p)
    if left != s["pending"]:
        _set(group["id"], pending=json.dumps(left))
    return left


def _follow_drain(group, s, domains, now):
    """Delete the member being drained once it stopped, or after DRAIN_MIN minutes. True while one is draining."""
    from app.core import privileged
    from app.daemon.registry import DaemonError
    from app.routers.vms import lifecycle

    name = s.get("draining")
    if not name:
        return False
    if name not in domains:
        _set(group["id"], draining=None, draining_since=None)
        return False
    late = (now - datetime.fromisoformat(s["draining_since"])).total_seconds() > DRAIN_MIN * 60
    if domains[name] and not late:
        return True
    try:
        if domains[name]:
            privileged.run("vm.power", user="system", vm=name, action="stop")
        lifecycle.delete_vm(name, confirm=True, node=None, purge=False, user=SYSTEM)
    except (DaemonError, Exception) as e:  # kept draining: tried again next minute, said in the journal
        logger.warning("Autoscaling: %s not removed: %s", name, getattr(e, "detail", e))
        return True
    with get_conn() as db:
        db.execute("UPDATE vm_groups SET count = MAX(count - 1, 0) WHERE id = ?", (group["id"],))
        db.commit()
    from app.core import vm_groups

    vm_groups._set_made(group["id"], [n for n in group.get("made") or [] if n != name])
    _set(group["id"], draining=None, draining_since=None)
    return False


def _removable(members):
    """The members autoscaling may remove: never one a protection holds (app/core/protections.py)."""
    from app.core import protections

    return [m for m in members if not protections.refusal("vm", m[1], deleting=True)]


def decide(group, s, members, avg, now):
    """('up', n) / ('down', name) / None, and the reason, for a group's settings and its members' average CPU."""
    count = len(members)
    running = [m for m in members if m[2]]
    removable = _removable(members)
    if count < s["min_count"]:
        return ("up", s["min_count"] - count), f"{count} VM(s), under the minimum of {s['min_count']}"
    if count > s["max_count"] and removable:
        victim = sorted(removable, key=lambda m: (m[2], -m[0]))[0][1]
        return ("down", victim), f"{count} VM(s), over the maximum of {s['max_count']}"
    if (
        s["last_action_at"]
        and (now - datetime.fromisoformat(s["last_action_at"])).total_seconds() < s["cooldown_min"] * 60
    ):
        return None, "cool-down"
    if avg is None or not running:
        return None, "not enough measures yet"
    if avg > s["up_pct"] and count < s["max_count"]:
        n = min(s["step"], s["max_count"] - count)
        return ("up", n), f"average CPU {avg:.0f} % over {s['window_min']} min, above {s['up_pct']:.0f} %"
    if avg < s["down_pct"] and count > s["min_count"] and removable:
        victim = sorted(removable, key=lambda m: (m[2], -m[0]))[0][1]
        return ("down", victim), f"average CPU {avg:.0f} % over {s['window_min']} min, below {s['down_pct']:.0f} %"
    return None, "within the thresholds"


def tick(now=None):
    """One decision per group that follows its load (every minute)."""
    from app.core import privileged, vm_groups
    from app.core.audit import log_action
    from app.daemon.registry import DaemonError

    now = now or _now()
    settings = all_settings()
    if not any(s["enabled"] for s in settings.values()):
        return
    domains = _domains()
    for group in vm_groups.groups():
        s = settings.get(group["id"])
        if not s or not s["enabled"]:
            continue
        if _follow_pending(group, s, domains, now) or _follow_drain(group, s, domains, now):
            continue  # a decision still under way
        members = vm_groups.members(group, domains)
        avg = average_cpu({name for _, name, running in members if running}, s["window_min"], now)
        what, reason = decide(group, s, members, avg, now)
        if what is None:
            continue
        try:
            if what[0] == "up":
                done = vm_groups.scale(group["id"], len(members) + what[1], SYSTEM)
                pending = [{"name": n, "since": now.isoformat()} for n in done["made"]]
                _record(group["id"], f"+{len(done['made'])}", reason, now, pending=pending)
                log_action(
                    "system", "vm_group_autoscale", group["name"], "success", f"+{', '.join(done['made'])}: {reason}"
                )
            else:
                # A stopped member is drained as it is: asking it to shut down was refused, and nothing was ever removed.
                if domains.get(what[1]):
                    privileged.run("vm.power", user="system", vm=what[1], action="shutdown")
                _record(group["id"], f"-{what[1]}", reason, now, draining=what[1])
                log_action("system", "vm_group_autoscale", group["name"], "success", f"-{what[1]}: {reason}")
        except (DaemonError, vm_groups.GroupError, Exception) as e:
            detail = getattr(e, "detail", str(e))
            _record(group["id"], "failed", f"{reason}: {detail}", now)
            log_action("system", "vm_group_autoscale", group["name"], "failed", detail)
