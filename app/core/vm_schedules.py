"""VM schedules: a VM started, shut down, stopped or snapshotted at set times of chosen weekdays.

A schedule names its VM by name, not its node: each node's scheduler runs the schedules of the VMs its own libvirt
hosts, so a VM that migrates keeps its schedules and they run where it now is. The times are the node's local time.

A snapshot schedule keeps its last `keep` snapshots: the names it made are kept on the schedule, and the oldest beyond
`keep` are deleted after each new one. Snapshots taken by hand are never touched.

Every run goes through hyperlite-daemon (vm.power, vm.snapshot_create, vm.snapshot_delete) under the account that
wrote the schedule, and is written to the journal.
"""

import logging
import re
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from app.core.audit import log_action

logger = logging.getLogger(__name__)

ACTIONS = ("start", "shutdown", "stop", "snapshot")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
VM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_KEEP = 100
MAX_PER_VM = 20
CHECK_INTERVAL_S = 30
# A run more than this late (the node was down) is skipped, not done at a time nobody chose.
LATE_LIMIT = timedelta(minutes=30)


def _store():
    from app.repositories.sqlite.vm_schedules import SqliteVmSchedulesStore

    return SqliteVmSchedulesStore()


def _now():
    return datetime.now(UTC)


def next_run(days, at, after):
    """The first moment after `after` (aware) that falls on one of `days` (0 = Monday) at `at` ("HH:MM"), in the
    node's local time; returned in UTC."""
    hh, mm = (int(x) for x in at.split(":"))
    local = after.astimezone()
    candidate = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    for _ in range(8):
        if candidate > local and candidate.weekday() in days:
            return candidate.astimezone(UTC)
        candidate = (candidate + timedelta(days=1)).replace(hour=hh, minute=mm)
    raise ValueError("A schedule needs at least one day")


def validate(payload):
    vm = str(payload.get("vm_name") or "")
    if not VM_RE.match(vm):
        raise ValueError("Not a VM name")
    action = payload.get("action")
    if action not in ACTIONS:
        raise ValueError(f"The action must be one of {', '.join(ACTIONS)}")
    days = payload.get("days")
    if not isinstance(days, list) or not days or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
        raise ValueError("Days are a non-empty list of weekdays, 0 (Monday) to 6 (Sunday)")
    at = str(payload.get("time") or "")
    if not TIME_RE.match(at):
        raise ValueError("The time is HH:MM")
    keep = payload.get("keep", 7)
    if not isinstance(keep, int) or not 1 <= keep <= MAX_KEEP:
        raise ValueError(f"Keep 1 to {MAX_KEEP} snapshots")
    return {
        "vm_name": vm,
        "action": action,
        "days": sorted(set(days)),
        "time": at,
        "keep": keep,
        "enabled": bool(payload.get("enabled", True)),
    }


def schedules(vm_name=None):
    return _store().schedules(vm_name)


def schedule(schedule_id):
    return _store().schedule(schedule_id)


def create(payload, username):
    clean = validate(payload)
    if len(_store().schedules(clean["vm_name"])) >= MAX_PER_VM:
        raise ValueError(f"At most {MAX_PER_VM} schedules per VM")
    schedule_id = uuid.uuid4().hex[:16]
    now = _now()
    _store().create(
        schedule_id, clean, next_run(clean["days"], clean["time"], now).isoformat(), username, now.isoformat()
    )
    return schedule(schedule_id)


def update(schedule_id, payload):
    """None when there is no such schedule. Its VM is kept: a schedule does not move to another VM."""
    current = schedule(schedule_id)
    if not current:
        return None
    clean = validate({**payload, "vm_name": current["vm_name"]})
    _store().update(schedule_id, clean, next_run(clean["days"], clean["time"], _now()).isoformat())
    return schedule(schedule_id)


def delete(schedule_id):
    return _store().delete(schedule_id)


def forget_vm(vm_name):
    """A deleted VM's schedules go with it."""
    for s in _store().schedules(vm_name):
        _store().delete(s["id"])


# Running


def _hosted():
    from app.core.libvirt_utils import open_conn

    conn = open_conn(readonly=True)
    try:
        return {d.name() for d in conn.listAllDomains()}
    finally:
        conn.close()


def snapshot_name(s, when):
    return f"sched-{s['id'][:6]}-{when.astimezone():%Y%m%d-%H%M}"


def _run(s, now):
    """Runs one schedule; returns what to record of it."""
    from app.core import privileged

    user = s.get("created_by") or "scheduler"
    if s["action"] == "snapshot":
        name = snapshot_name(s, now)
        privileged.run(
            "vm.snapshot_create", user=user, timeout=600, vm=s["vm_name"], name=name, description="Scheduled snapshot"
        )
        made = [*s["made"], name]
        while len(made) > s["keep"]:
            old = made.pop(0)
            try:
                privileged.run("vm.snapshot_delete", user=user, timeout=600, vm=s["vm_name"], name=old)
            except Exception as e:
                if getattr(e, "kind", None) != "not_found":  # deleted by hand meanwhile: nothing left to do
                    made.insert(0, old)
                    logger.warning("Scheduled snapshot %s of %s not deleted: %s", old, s["vm_name"], e)
                    break
        return f"snapshot {name}", made
    privileged.run("vm.power", user=user, vm=s["vm_name"], action=s["action"])
    return s["action"], s["made"]


def run_due(now=None):
    """The due schedules of the VMs this node hosts, each run once and moved to its next time."""
    now = now or _now()
    due = _store().due(now.isoformat())
    if not due:
        return
    hosted = _hosted()
    for s in due:
        if s["vm_name"] not in hosted:
            continue  # the node hosting the VM runs it
        upcoming = next_run(s["days"], s["time"], now).isoformat()
        if now - datetime.fromisoformat(s["next_run"]) > LATE_LIMIT:
            _store().record(s["id"], s["last_run"], s["last_result"], s["made"], upcoming)
            log_action(
                "scheduler",
                f"schedule_{s['action']}",
                s["vm_name"],
                "failed",
                "Skipped: the node was not running at the time",
            )
            continue
        try:
            what, made = _run(s, now)
            _store().record(s["id"], now.isoformat(), "succeeded", made, upcoming)
            log_action(
                s.get("created_by") or "scheduler",
                f"schedule_{s['action']}",
                s["vm_name"],
                "success",
                f"Scheduled: {what}",
            )
        except Exception as e:
            detail = getattr(e, "detail", None) or str(e)
            _store().record(s["id"], now.isoformat(), f"failed: {detail}"[:500], s["made"], upcoming)
            log_action(s.get("created_by") or "scheduler", f"schedule_{s['action']}", s["vm_name"], "failed", detail)


def _loop():
    from app.core import cluster_lead

    while True:
        try:
            if not cluster_lead.paused("VM schedules"):
                run_due()
        except Exception:
            logger.exception("VM schedules tick failed")
        time.sleep(CHECK_INTERVAL_S)


def start_vm_schedules():
    threading.Thread(target=_loop, daemon=True, name="vm-schedules").start()
