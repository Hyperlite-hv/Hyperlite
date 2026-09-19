"""Automatic deletion of inactive VMs. An opt-in option chosen at creation
("delete if stopped for N days"), meant for lab and test VMs that get
forgotten. Disabled by default, VM by VM.

Safety rules (checked in this order, each one a silent "skip" for THIS VM that
must never interrupt the cycle for the others):
- A VM that is currently RUNNING is never deleted, whatever the threshold: the
  counter only advances while the VM is STOPPED (see
  app/core/vm_meta.py::touch_vm_activity, called on every start, which resets
  it).
- A VM protected by HA is never deleted automatically: an HA VM is by
  definition considered critical, the exact opposite of a disposable VM.
- A warning (notification) is sent ~24 h before the real deletion, so there is
  no surprise deletion on the first cycle that detects the threshold being
  exceeded.

"""

import threading
import time
from datetime import UTC, datetime

import libvirt

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.libvirt_utils import open_conn
from app.core.vm_meta import delete_vm_auto_cleanup, list_all_auto_cleanup

CHECK_INTERVAL_S = 3600  # a threshold is counted in DAYS, hourly is frequent enough
WARNING_HOURS_BEFORE = 24


def _age_hours(iso_ts):
    dt = datetime.fromisoformat(iso_ts)
    return (datetime.now(UTC) - dt).total_seconds() / 3600


def _mark_warned(vm_name):
    with get_conn() as db:
        db.execute(
            "UPDATE vm_auto_cleanup SET warned_at = ? WHERE vm_name = ?",
            (datetime.now(UTC).isoformat(), vm_name),
        )
        db.commit()


def check_once():
    from app.core.ha import get_protected  # late import: avoids a cycle when the module loads
    from app.routers.vms.lifecycle import _perform_vm_deletion

    rows = list_all_auto_cleanup()
    if not rows:
        return

    conn = open_conn()
    try:
        for row in rows:
            vm_name = row["vm_name"]
            try:
                domain = conn.lookupByName(vm_name)
            except libvirt.libvirtError:
                # VM already deleted through another path (an admin, the regular DELETE
                # /vms/{name}): clean up the orphaned entry instead of retrying it forever on
                # every cycle.
                delete_vm_auto_cleanup(vm_name)
                continue

            if domain.isActive():
                continue  # the counter only runs while the VM is stopped

            if get_protected(vm_name):
                continue  # HA VM: never touched automatically

            age_h = _age_hours(row["last_active_at"])
            threshold_h = row["inactive_days"] * 24

            if age_h < threshold_h - WARNING_HOURS_BEFORE:
                continue  # still far from the threshold, nothing to do

            if age_h < threshold_h:
                if not row["warned_at"]:
                    _mark_warned(vm_name)
                    msg = (
                        f"VM '{vm_name}' will be deleted automatically in ~24 h "
                        f"(stopped for {row['inactive_days']}+ days, threshold set at creation)"
                    )
                    log_action("system", "auto_cleanup_warning", vm_name, "succes", msg)
                continue

            # Threshold exceeded: real deletion.
            try:
                _perform_vm_deletion(conn, domain, vm_name)
                msg = f"Automatic deletion: stopped for {row['inactive_days']}+ days (threshold set at creation)"
                log_action("system", "delete_vm", vm_name, "succes", msg)
            except Exception as e:
                log_action("system", "delete_vm", vm_name, "echec", f"Automatic deletion failed: {e!r}")
    finally:
        conn.close()


def _loop():
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"[vm_cleanup] cycle failed: {e!r}", flush=True)
        time.sleep(CHECK_INTERVAL_S)


def start_auto_cleanup_scheduler():
    threading.Thread(target=_loop, daemon=True, name="vm-auto-cleanup").start()
