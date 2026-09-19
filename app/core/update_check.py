"""Periodic automatic check for Hyperlite updates.

Prompted by finding a production node silently several versions behind: its
APT repository pointed at a stale mirror, and nothing flagged the gap until an
admin clicked "Check for updates" by hand.

Same cautious principle as HA: this module DETECTS and ALERTS, it NEVER applies
an update by itself. An update restarts the service (running VMs are not
affected, see app/routers/update.py), which is not something to do without
explicit human supervision.

It reuses check_update() directly (same logic as GET /update/check, git or apt
depending on _install_method()) instead of duplicating the detection. It
notifies through the single entry point audit.py::log_action(), ONCE per
detected remote version (update_check_state, a one-row table), not on every
hourly cycle while nobody applies the update, which would send one webhook or
email per hour indefinitely.

"""

import threading
import time
from datetime import UTC, datetime

from app.core.audit import log_action
from app.core.database import get_conn

CHECK_INTERVAL_S = (
    3600  # same cadence as vm_cleanup.py: version drift is counted in hours or days, no need to check more often
)


def _get_last_notified():
    with get_conn() as db:
        row = db.execute("SELECT last_notified_version FROM update_check_state WHERE id = 1").fetchone()
        return row["last_notified_version"] if row else None


def _mark_notified(version):
    now = datetime.now(UTC).isoformat()
    with get_conn() as db:
        db.execute(
            """
            INSERT INTO update_check_state (id, last_notified_version, last_checked_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_notified_version = excluded.last_notified_version,
                                           last_checked_at = excluded.last_checked_at
            """,
            (version, now),
        )
        db.commit()


def _touch_checked_at():
    now = datetime.now(UTC).isoformat()
    with get_conn() as db:
        db.execute(
            """
            INSERT INTO update_check_state (id, last_checked_at)
            VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_checked_at = excluded.last_checked_at
            """,
            (now,),
        )
        db.commit()


def check_once():
    from app.routers.update import check_update  # late import: avoids a cycle when the module loads

    result = check_update(user={"role": "admin"})
    _touch_checked_at()

    if not result.get("verifiable") or result.get("a_jour"):
        return

    remote = result.get("commit_distant")
    if not remote or _get_last_notified() == remote:
        return  # no usable remote version, or already notified for THIS version

    msg = f"New Hyperlite version available: {remote} (current version: {result.get('commit_local')})"
    log_action("system", "update_available", "hyperlite", "succes", msg)
    _mark_notified(remote)


def _loop():
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"[update_check] cycle failed: {e!r}", flush=True)
        time.sleep(CHECK_INTERVAL_S)


def start_update_check_scheduler():
    threading.Thread(target=_loop, daemon=True, name="update-auto-check").start()
