"""Automatic deletion of inactive VMs. An opt-in option chosen at creation
("delete if stopped for N days"), meant for lab and test VMs that get
forgotten. Disabled by default, VM by VM.

Safety rules (checked in this order, each one a "skip" for THIS VM that must
never interrupt the cycle for the others; an unexpected error on one VM is
logged and audited, and the cycle goes on with the next one):
- A VM that is currently RUNNING is never deleted, whatever the threshold: the
  counter only advances while the VM is STOPPED (see
  app/core/vm_meta.py::touch_vm_activity, called on every start, which resets
  it).
- A VM protected by HA is never deleted automatically: an HA VM is by
  definition considered critical, the exact opposite of a disposable VM.
- A warning (notification) is sent at least WARNING_HOURS_BEFORE hours before
  the real deletion, and a VM is only deleted once that warning has been out for
  that long. When the service was down during the warning window, the warning
  goes out late and the deletion is postponed accordingly: never a deletion that
  nobody was told about.
- A VM busy with another operation (backup, restore, migration...) is left for
  the next cycle.

"""

import logging
import threading
import time
from datetime import UTC, datetime

import libvirt

from app.core import vm_locks
from app.core.audit import log_action
from app.core.database import get_conn
from app.core.libvirt_utils import open_conn
from app.core.vm_meta import delete_vm_auto_cleanup, list_all_auto_cleanup

CHECK_INTERVAL_S = 3600  # a threshold is counted in DAYS, hourly is frequent enough
WARNING_HOURS_BEFORE = 24

logger = logging.getLogger(__name__)


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
    rows = list_all_auto_cleanup()
    if not rows:
        return

    conn = open_conn()
    try:
        for row in rows:
            try:
                _check_vm(conn, row)
            except Exception as e:
                logger.exception("Automatic deletion check of %s failed", row["vm_name"])
                log_action("system", "auto_cleanup_check", row["vm_name"], "echec", f"Check failed: {e!r}"[:500])
    finally:
        conn.close()


def _warn(vm_name, inactive_days):
    _mark_warned(vm_name)
    msg = (
        f"VM '{vm_name}' will be deleted automatically in {WARNING_HOURS_BEFORE} h or more "
        f"(stopped for {inactive_days}+ days, threshold set at creation)"
    )
    log_action("system", "auto_cleanup_warning", vm_name, "succes", msg)


def _check_vm(conn, row):
    from app.core.ha import get_protected  # late import: avoids a cycle when the module loads
    from app.routers.vms.lifecycle import _perform_vm_deletion

    vm_name = row["vm_name"]
    try:
        domain = conn.lookupByName(vm_name)
    except libvirt.libvirtError:
        # VM already deleted through another path (an admin, the regular DELETE
        # /vms/{name}): clean up the orphaned entry instead of retrying it forever on
        # every cycle.
        delete_vm_auto_cleanup(vm_name)
        return

    if domain.isActive():
        return  # the counter only runs while the VM is stopped

    if get_protected(vm_name):
        return  # HA VM: never touched automatically

    age_h = _age_hours(row["last_active_at"])
    threshold_h = row["inactive_days"] * 24

    if age_h < threshold_h - WARNING_HOURS_BEFORE:
        return  # still far from the threshold, nothing to do

    if not row["warned_at"]:
        # First cycle in the warning window, or a late one (the service was down): warn now, delete later.
        _warn(vm_name, row["inactive_days"])
        return

    if age_h < threshold_h or _age_hours(row["warned_at"]) < WARNING_HOURS_BEFORE:
        return  # the threshold, or the notice promised by the warning, is not reached yet

    try:
        claim = vm_locks.claim(vm_name, "an automatic deletion")
    except vm_locks.VmBusy:
        return  # busy with another operation: the next cycle will see it again

    with claim:
        # Checked again under the claim: the VM may have been started meanwhile.
        if domain.isActive():
            return
        try:
            _perform_vm_deletion(conn, domain, vm_name)
            msg = f"Automatic deletion: stopped for {row['inactive_days']}+ days (threshold set at creation)"
            log_action("system", "delete_vm", vm_name, "succes", msg)
        except Exception as e:
            log_action("system", "delete_vm", vm_name, "echec", f"Automatic deletion failed: {e!r}")


def _loop():
    while True:
        try:
            check_once()
        except Exception:
            logger.exception("Automatic deletion cycle failed")
        time.sleep(CHECK_INTERVAL_S)


def start_auto_cleanup_scheduler():
    threading.Thread(target=_loop, daemon=True, name="vm-auto-cleanup").start()
