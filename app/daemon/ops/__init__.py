"""The operations of hyperlite-daemon, one module per domain. Importing this package registers them all."""

from app.daemon.ops import auth, host, hyperlite, system  # noqa: F401
