"""What each operation needs of the person who asks (docs/design/privilege-separation.md, section 14, 4d), checked by
hyperlite-daemon against its own store of the permissions (step 4b), as pvedaemon checks each API method's
`permissions` against user.cfg whatever pveproxy says.

The caller is the account its proof proves (section 14, 4a): a session, an API token, a task ticket, or a ticket
another member signed. A call without a proof comes from a service of this node, never from a person:
- in process (the daemon's own code, the test suite);
- from root on the socket (hyperlite-scheduler, HA's services; and the API until it runs as www-data, step 4e).

Anyone else without a proof is refused. A proof that proves nothing is refused too."""

import os
from dataclasses import dataclass

from app.daemon.registry import DaemonError


@dataclass(frozen=True)
class Need:
    """`kind`: "admin", "anyone" (any proven account), "public" (no proof: the sign-in itself), "own" (the account
    named in `arg`, or an administrator), a privilege on the object named in `arg` ("vm", "container", "storage",
    "network"), "vms" (that privilege on each VM of the list `arg`), or "rule" (`rule(user, args)` says why not)."""

    kind: str
    privilege: str | None = None
    arg: str | None = None
    rule: object = None

    def refusal(self, user, args):
        """Why `user` (a users row) may not, or None."""
        if user["role"] == "admin" or self.kind == "anyone":
            return None
        if self.kind == "admin":
            return "Only an administrator may do this"
        if self.kind == "rule":
            return self.rule(user, args)
        value = getattr(args, self.arg, None)
        if self.kind == "vms":
            refused = [v for v in value or () if on_vm(self.privilege).refusal(user, _Named(v))]
            return (
                f"Insufficient rights on VM '{refused[0]}' (required privilege: {self.privilege})" if refused else None
            )
        if self.kind == "own":
            return None if value == user["username"] else "Only the account itself or an administrator may do this"
        from app.core import permissions

        if value is None:
            return f"Insufficient rights (required privilege: {self.privilege})"
        if self.kind == "vm":
            allowed = permissions.has_privilege(user, value, self.privilege)
        elif self.kind == "container":
            allowed = permissions.has_container_privilege(user, value, self.privilege)
        else:
            allowed = permissions.has_scoped_privilege(user, self.kind, value, self.privilege)
        return (
            None if allowed else f"Insufficient rights on {self.kind} '{value}' (required privilege: {self.privilege})"
        )


ADMIN = Need("admin")
ANYONE = Need("anyone")
PUBLIC = Need("public")


class _Named:
    def __init__(self, vm):
        self.vm = vm


def on_vms(privilege, arg="vms"):
    return Need("vms", privilege, arg)


def by_rule(rule):
    """`rule(user, args)`: why that account may not, or None (an administrator always may)."""
    return Need("rule", rule=rule)


def on_vm(privilege, arg="vm"):
    return Need("vm", privilege, arg)


def on_container(privilege, arg="container"):
    return Need("container", privilege, arg)


def on_storage(privilege, arg="pool"):
    return Need("storage", privilege, arg)


def on_network(privilege, arg="network"):
    return Need("network", privilege, arg)


def own(arg="username"):
    return Need("own", arg=arg)


def check(op, need, ctx, args):
    """Raises DaemonError("forbidden") unless the caller may run `op`; returns the proven account's name, or None
    for a service of this node."""
    if need.kind == "public":
        return None
    from app.daemon.registry import proven

    if ctx.proof:
        who = proven(ctx.proof)
        if who is None:
            raise DaemonError("forbidden", "The credential of this request proves no account")
        user = _account(who)
        if user is None:
            raise DaemonError("forbidden", f"No account '{who}'")
        refusal = need.refusal(user, args)
        if refusal:
            raise DaemonError("forbidden", refusal)
        return who
    if ctx.peer_pid == os.getpid() or ctx.peer_uid == 0:
        return None
    raise DaemonError("forbidden", f"{op} needs the proof of the account that asks")


def _account(username):
    from app.repositories import registry

    return registry.accounts().sync.get(username)
