"""python -m app.daemon: run hyperlite-daemon (installer/hyperlite-daemon.service)."""

import logging
import os
import sys

from app.daemon.server import serve


def _accounts_at_start():
    """The admin account on a fresh node, the initial password's checks and the second factors sealed (seed.first_start),
    before the socket answers: the API starts once it does, and finds the account made. They run as one of the daemon's
    operations, so the accounts are written here rather than asked of a socket that does not answer yet
    (app/repositories/daemon_write.py). A failure is logged: the daemon starts all the same."""
    from app.core import seed
    from app.core.database import init_db
    from app.daemon import registry

    running = registry._running.set("daemon.start")
    try:
        init_db()  # a fresh node: the daemon starts first, before the API made the schema
        seed.first_start()
    except Exception:
        logging.getLogger(__name__).exception("The accounts' start failed")
    finally:
        registry._running.reset(running)


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("hyperlite-daemon runs as root")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from app.core import peer_auth

    _accounts_at_start()
    peer_auth.start_publishing()  # this node's public keys, for the other members (app/core/peer_auth.py)
    # The copy between SQLite and hyperlite-cfs, both ways: root's, as the permissions' file and the cluster's tree are
    # (app/daemon/ops/cfs.py).
    from app.repositories.cfs import inbound, shadow

    shadow.start_shadow_copy()
    inbound.start_apply_loop()
    # What else writes the cluster's tree, which hyperlite-cfs takes from root only (section 14.7): this node's
    # certificate fingerprint for the members' HTTPS calls, API v1's one rewrite, and the node's lead with Corosync's
    # configuration and the members' SSH keys following the shared ones (cluster_setup.sync).
    from app.core import cluster_lead, cluster_v1, peer_https

    peer_https.start()
    cluster_v1.start()
    cluster_lead.start_lead_loop(follow_setup=True)
    serve()
