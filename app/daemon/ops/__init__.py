"""The operations of hyperlite-daemon, one module per domain. Importing this package registers them all."""

from app.daemon.ops import (  # noqa: F401
    auth,
    cluster,
    console,
    container,
    disk,
    host,
    hyperlite,
    job,
    network,
    storage,
    system,
    tls,
    vm,
)
