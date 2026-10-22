"""The update job, run as root in a transient unit of its own that hyperlite-daemon starts (app/daemon/ops/hyperlite.py):
it outlives the restarts of the API and of the daemon that the package being installed causes.

    python -m app.daemon.update_job KIND TASK_ID USERNAME SYSTEM(0|1)"""

import sys

from app.daemon.ops.hyperlite import run_job

if __name__ == "__main__":
    kind, task_id, username, system = sys.argv[1:5]
    run_job(kind, task_id, username, system == "1")
