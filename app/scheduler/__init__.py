"""hyperlite-scheduler: the work no person asks for, out of the API (docs/design/privilege-separation.md, section
14, 4c), as Proxmox VE's pvescheduler runs vzdump's and replication's jobs apart from pveproxy and pvedaemon.

It runs the scheduled backups (and the backup groups' jobs, the stores' copies and garbage collections), the
replication, the inactive VMs' automatic deletion, the update check and the start at boot. Its tasks record it as
their runner (app/core/tasks.py): the dashboard lists and cancels them as the API's own. Its claims on VMs are the
shared ones (app/core/shared_claims.py).

An installed node runs it as its own service: its unit sets SEPARATE_ENV for the API too, which then starts none of
these. A checkout, the end-to-end backend and the test suite run them in the API, as before."""

import os

SEPARATE_ENV = "HYPERLITE_SCHEDULER_SERVICE"


def separate():
    """Whether hyperlite-scheduler runs the schedulers rather than the API."""
    return os.environ.get(SEPARATE_ENV) == "1"


def start():
    """Start every scheduler's thread in this process."""
    from app.core.backups import start_backup_scheduler
    from app.core.replication import start_replication_scheduler
    from app.core.update_check import start_update_check_scheduler
    from app.core.vm_boot import start_boot_sequence
    from app.core.vm_cleanup import start_auto_cleanup_scheduler

    start_backup_scheduler()
    start_replication_scheduler()
    start_auto_cleanup_scheduler()
    start_update_check_scheduler()
    start_boot_sequence()
