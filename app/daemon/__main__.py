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
    from app.core import disk_library

    # What a stop left being stored (app/core/disk_library.py): root's staging directory, which the API cannot read.
    if removed := disk_library.clean_staging():
        logging.getLogger(__name__).info("Removed %d upload(s) a stop left being stored", removed)
    serve()
