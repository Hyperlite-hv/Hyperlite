"""The operations of hyperlite-daemon, one module per domain. Importing this package registers them all."""

from app.daemon.ops import auth, container, disk, host, hyperlite, network, storage, system, tls, vm  # noqa: F401
