"""VM identities (docs/design/governance.md, section 9): a VM calls the API with an identity of its own.

The identity is a delegated API token (docs/design/access-2.md, section 7): one role on one perimeter, owned by the
account that turned it on and never wider than that account's rights. Hyperlite writes it into the guest through the
guest agent (/etc/hyperlite/vm-token, or C:\\ProgramData\\Hyperlite\\vm-token on Windows), readable by root (SYSTEM)
only, renews it a week before its 30 days end, and revokes it with the VM.
"""

from datetime import UTC, datetime, timedelta

from app.core import api_tokens, privileged
from app.core.database import get_conn

LIFETIME_DAYS = 30
RENEW_BEFORE_DAYS = 7
PATHS = {"sh": "/etc/hyperlite/vm-token", "powershell": r"C:\ProgramData\Hyperlite\vm-token"}


def _script(shell, token):
    if shell == "powershell":
        return (
            "New-Item -ItemType Directory -Force C:\\ProgramData\\Hyperlite | Out-Null; "
            f"Set-Content -NoNewline -Path C:\\ProgramData\\Hyperlite\\vm-token -Value '{token}'"
        )
    return (
        "umask 077 && mkdir -p /etc/hyperlite && "
        f"printf '%s\\n' '{token}' > /etc/hyperlite/vm-token.new && mv /etc/hyperlite/vm-token.new /etc/hyperlite/vm-token"
    )


def identities():
    with get_conn() as db:
        return [dict(r) for r in db.execute("SELECT * FROM vm_identities ORDER BY vm_name")]


def of(vm):
    return next((i for i in identities() if i["vm_name"] == vm), None)


def _issue(owner, vm, role, resource_type, resource_id):
    expires = (datetime.now(UTC) + timedelta(days=LIFETIME_DAYS)).isoformat()
    token_id, token = api_tokens.create_token(owner, f"vm:{vm}", expires_at=expires, kind="vm")
    api_tokens.set_scope(token_id, role, resource_type, resource_id)
    return token_id, token, expires


def _deliver(owner, vm, shell, token):
    """Writes the token into the guest; the error, or None."""
    from app.daemon.registry import DaemonError

    try:
        result = privileged.run(
            "vm.agent_exec", user=owner, vm=vm, node=None, shell=shell, script=_script(shell, token), timeout_s=30,
            timeout=45,
        )  # fmt: skip
    except DaemonError as e:
        return e.detail
    if result.get("exit_code") != 0:
        return (result.get("stderr") or "the guest refused the write").strip()[:300]
    return None


def enable(owner, vm, role, resource_type, resource_id, shell):
    """Turn the identity on (or replace it): a new token, written into the guest. The row, with `last_error` when the
    guest could not be written to (no agent): the identity then waits for the next renewal to be delivered."""
    old = of(vm)
    token_id, token, expires = _issue(owner, vm, role, resource_type, resource_id)
    error = _deliver(owner, vm, shell, token)
    now = datetime.now(UTC).isoformat()
    with get_conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO vm_identities (vm_name, token_id, owner, role, resource_type, resource_id, shell, "
            "expires_at, delivered_at, last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (vm, token_id, owner, role, resource_type, resource_id, shell, expires, None if error else now, error),
        )
        db.commit()
    if old:
        api_tokens.revoke_token(old["owner"], old["token_id"])
    return of(vm)


def disable(vm):
    """The token revoked and the identity forgotten (the VM deleted, or turned off by hand). True when there was one."""
    found = of(vm)
    if not found:
        return False
    api_tokens.revoke_token(found["owner"], found["token_id"])
    with get_conn() as db:
        db.execute("DELETE FROM vm_identities WHERE vm_name = ?", (vm,))
        db.commit()
    return True


def renew_due(now=None):
    """Renew the identities ending within a week, and retry those never delivered (hyperlite-scheduler)."""
    soon = ((now or datetime.now(UTC)) + timedelta(days=RENEW_BEFORE_DAYS)).isoformat()
    for i in identities():
        if i["expires_at"] <= soon or i["delivered_at"] is None:
            enable(i["owner"], i["vm_name"], i["role"], i["resource_type"], i["resource_id"], i["shell"])
