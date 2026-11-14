"""The recycle bin (docs/design/governance.md, section 7): a deleted VM is kept for some days before its disks go.

Deleting a VM with the bin on takes it out of libvirt (hyperlite-daemon keeps its definition and its snapshots' in a
file only root reads, app/daemon/ops/vm.py) and leaves its disks, NVRAM, pools, rights and notes in place; its name
stays taken. It comes back as it was until `purge_at`, when hyperlite-scheduler deletes it for good, disks included,
as DELETE /vms/{name} would have. The bin is this node's: a VM is binned on the node it ran on.
"""

import json
from datetime import UTC, datetime, timedelta

from app.core import privileged
from app.core.database import get_conn

SETTING = "recycle_bin_days"
DEFAULT_DAYS = 7


def _settings():
    from app.repositories import registry

    return registry.settings().sync


def days():
    raw = _settings().app_setting(SETTING)
    return int(raw) if raw not in (None, "") and str(raw).isdigit() else DEFAULT_DAYS


def set_days(n):
    _settings().set_app_setting(SETTING, str(int(n)))


def items():
    with get_conn() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM recycle_bin ORDER BY deleted_at DESC")]
    for r in rows:
        r["disks"] = json.loads(r["disks"] or "[]")
    return rows


def holds(name):
    """True when a VM of that name is in this node's bin: its name stays taken."""
    import sqlite3

    try:
        with get_conn() as db:
            return db.execute("SELECT 1 FROM recycle_bin WHERE vm_name = ?", (name,)).fetchone() is not None
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return False  # a database made before the bin (hyperlite-daemon's own checks in the test suite)


def put(domain, username):
    """Bin a stopped VM of this node; returns its row."""
    info = domain.info()
    name = domain.name()
    answer = privileged.run("vm.bin", user=username, vm=name)
    now = datetime.now(UTC)
    row = (name, username, now.isoformat(), (now + timedelta(days=days())).isoformat(), json.dumps(answer["disks"]),
           info[3], info[1] // 1024)  # fmt: skip
    with get_conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO recycle_bin (vm_name, deleted_by, deleted_at, purge_at, disks, vcpu, memory_mb) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            row,
        )
        db.commit()
    from app.core import inventory_cache

    inventory_cache.invalidate()
    return next(i for i in items() if i["vm_name"] == name)


def _forget(name):
    with get_conn() as db:
        db.execute("DELETE FROM recycle_bin WHERE vm_name = ?", (name,))
        db.commit()
    from app.core import inventory_cache

    inventory_cache.invalidate()


def restore(name, username):
    privileged.run("vm.unbin", user=username, vm=name)
    _forget(name)


def purge(name, username):
    """Delete it for good: its disks go. Returns the disks kept ({path: reason}), as a deletion does."""
    from app.core import vm_records, vm_schedules

    kept = privileged.run("vm.bin_purge", user=username, vm=name, timeout=600)["kept"]
    from app.core import vm_identity

    vm_identity.disable(name)
    vm_records.forget(name, None)
    vm_schedules.forget_vm(name)
    _forget(name)
    return kept


def purge_due(now=None):
    """Purge what is past its date (hyperlite-scheduler, with the automatic deletion of inactive VMs)."""
    from app.core.audit import log_action

    now = (now or datetime.now(UTC)).isoformat()
    for item in items():
        if item["purge_at"] <= now:
            try:
                kept = purge(item["vm_name"], "system")
                note = "; ".join(f"disk {p} kept ({r})" for p, r in kept.items())
                log_action("system", "delete_vm", item["vm_name"], "success", f"Recycle bin emptied {note}".strip())
            except Exception as e:  # the next cycle tries again; the VM stays in the bin meanwhile
                log_action("system", "delete_vm", item["vm_name"], "failed", f"Recycle bin not emptied: {e!r}")
