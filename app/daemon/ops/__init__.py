"""The operations of hyperlite-daemon, one module per domain. Importing this package registers them all."""

from app.daemon.ops import auth, host, hyperlite, network, system, tls  # noqa: F401
