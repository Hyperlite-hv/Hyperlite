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
from pathlib import Path

from app.core.audit import log_action


def _store():
    from app.repositories import registry

    return registry.settings().sync


CHECK_INTERVAL_S = (
    3600  # same cadence as vm_cleanup.py: version drift is counted in hours or days, no need to check more often
)


SOURCE_PROBLEM_MARKER = "apt-source-problem"


def _get_last_notified():
    return _store().last_notified_version()


def _mark_notified(version):
    _store().mark_update_notified(version, datetime.now(UTC).isoformat())


def _touch_checked_at():
    _store().touch_update_checked(datetime.now(UTC).isoformat())


REBOOT_MARK = "reboot_notified_boot"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def check_reboot_needed():
    """Notify once per boot that the node waits for a reboot to use installed updates (the night's security updates
    never reboot by themselves)."""
    from app.core import host_system

    if not host_system.REBOOT_REQUIRED.exists():
        return False
    try:
        boot = BOOT_ID.read_text().strip()
    except OSError:
        boot = "unknown"
    if _store().app_setting(REBOOT_MARK) == boot:
        return False
    pkgs = host_system.REBOOT_PKGS.read_text().split() if host_system.REBOOT_PKGS.exists() else []
    detail = f"for {', '.join(sorted(set(pkgs)))[:300]}" if pkgs else None
    log_action("system", "host_reboot_required", "host", "succes", detail)
    _store().set_app_setting(REBOOT_MARK, boot)
    return True


def check_once():
    from app.routers.update import check_update  # late import: avoids a cycle when the module loads

    check_reboot_needed()

    result = check_update(user={"role": "admin"})
    _touch_checked_at()

    # The APT source is missing or outdated: no new version can ever be seen, which is exactly how a node
    # stays behind silently. Alert once (the marker is replaced by the next real version notification).
    if result.get("source_problem"):
        if _get_last_notified() != SOURCE_PROBLEM_MARKER:
            log_action("system", "update_check_blocked", "hyperlite", "echec", result.get("erreur"))
            _mark_notified(SOURCE_PROBLEM_MARKER)
        return

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
