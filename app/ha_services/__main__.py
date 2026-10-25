"""python -m app.ha_services lrm|crm: run hyperlite-ha-lrm or hyperlite-ha-crm (installer/hyperlite-ha-*.service)."""

import logging
import os
import signal
import sys
import threading


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("lrm", "crm"):
        sys.exit("usage: python -m app.ha_services lrm|crm")
    if os.geteuid() != 0:
        sys.exit("HA's services run as root")
    from app import ha_services as ha

    role = ha.LRM if argv[0] == "lrm" else ha.CRM
    os.environ.setdefault("HYPERLITE_SERVICE", role)  # its tasks' runner (app/core/tasks.py), and ha.here()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from app.core import cluster_lead, ha_selffence, statd, tasks
    from app.core.database import init_db

    init_db()
    tasks.close_interrupted_tasks()
    if role == ha.LRM:
        statd.start()  # the node's heartbeat, with the watchdog's feeding
    else:
        cluster_lead.start_lead_loop(follow_setup=False)  # the recovery and the evaluation act on the leader only
    ha.start(role)
    logging.getLogger(__name__).info("hyperlite-%s started", role)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    if role == ha.LRM:
        # A normal stop (an update, a restart) disarms the watchdog and says so: the node must not be reset for it.
        ha_selffence.stop()


if __name__ == "__main__":
    main()
