"""python -m app.daemon: run hyperlite-daemon (installer/hyperlite-daemon.service)."""

import logging
import os
import sys

from app.daemon.server import serve

if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("hyperlite-daemon runs as root")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from app.core import peer_auth

    peer_auth.start_publishing()  # this node's public keys, for the other members (app/core/peer_auth.py)
    from app.core import disk_library

    # What a stop left being stored (app/core/disk_library.py): root's staging directory, which the API cannot read.
    if removed := disk_library.clean_staging():
        logging.getLogger(__name__).info("Removed %d upload(s) a stop left being stored", removed)
    serve()
