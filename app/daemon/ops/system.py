"""The daemon about itself. The domains of the API move here one at a time (docs/design/privilege-separation.md, step 3)."""

import os

from app.core import version
from app.daemon.needs import PUBLIC
from app.daemon.registry import operation


@operation("system.ping", needs=PUBLIC)
def ping(args, ctx):
    """Whether the daemon answers, and which version and user it runs as: what the API checks at start-up."""
    return {"version": version.STARTUP_VERSION, "uid": os.geteuid()}
