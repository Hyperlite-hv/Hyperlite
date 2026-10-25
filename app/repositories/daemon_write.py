"""The permissions written by hyperlite-daemon only (docs/design/privilege-separation.md, section 14.1, step 4b), as
pvedaemon is the only writer of Proxmox VE's user.cfg and priv/: the accounts, API tokens, security keys, second
factors, revoked sessions, groups, roles, resource pools and ACLs.

A repository method marked @daemon_write(op) runs its SQL inside one of the daemon's operations (app/daemon/ops/
access.py, or in process: the test suite, the e2e backend). Anywhere else (the API), it becomes that operation, with
the method's own arguments: no caller changes, and the API never writes these tables."""

import functools
import inspect

from app.domain.common import AlreadyExists
from app.repositories.cfs import confirm, ids


def daemon_write(op):
    def mark(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def call(self, *args, **kwargs):
            from app.daemon.registry import DaemonError, running_here

            if running_here():
                return fn(self, *args, **kwargs)
            from app.core import privileged

            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            arguments = dict(list(bound.arguments.items())[1:])
            try:
                answer = privileged.write(op, **arguments)
            except DaemonError as e:
                # A unique name or key already taken: the exception the method raises, so its callers are unchanged.
                if e.kind == "in_use":
                    raise AlreadyExists(e.detail) from None
                # No id for a new row (the cluster's configuration service does not answer): its own 503.
                if e.kind == "unavailable" and e.detail == ids.NO_ID_DETAIL:
                    raise ids.NoId(e.detail) from None
                raise
            confirm.note(answer["outbox"])
            return answer["result"]

        call.daemon_op = op
        return call

    return mark
