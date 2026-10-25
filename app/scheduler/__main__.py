"""python -m app.scheduler: run hyperlite-scheduler (installer/hyperlite-scheduler.service)."""

import logging
import os
import signal
import sys
import threading


def main():
    if os.geteuid() != 0:
        sys.exit("hyperlite-scheduler runs as root")
    os.environ.setdefault("HYPERLITE_SERVICE", "scheduler")  # its tasks' runner (app/core/tasks.py)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from app import scheduler
    from app.core import cluster_lead, tasks
    from app.core.database import init_db

    init_db()
    # The tasks its previous process left: the API's and HA's live ones are theirs.
    tasks.close_interrupted_tasks()
    cluster_lead.start_lead_loop(follow_setup=False)  # the stores' copies run on the leading node only
    scheduler.start()
    logging.getLogger(__name__).info("hyperlite-scheduler started")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()


if __name__ == "__main__":
    main()
