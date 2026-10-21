"""Sessions (docs/design/privilege-separation.md, section 12): the daemon holds the key pairs that sign them, the API
only verifies (app/core/auth_keys.py)."""

import time
from typing import Any

from app.core import auth_keys
from app.daemon.registry import Args, DaemonError, operation

# The longest session the API opens ("Stay signed in", app/core/security.py), with a margin for clocks.
MAX_LIFETIME_S = 7 * 24 * 3600 + 300


class SignArgs(Args):
    claims: dict[str, Any]


@operation("auth.sign", SignArgs)
def sign(args, ctx):
    """A session token for the claims the API sends. Transitional (section 12, PR A): the API still checks the
    credentials itself, so it is trusted with what it asks to sign; PR B moves the checks here and removes this
    operation. Refused: a token without an account, or living longer than any session the API opens."""
    claims = args.claims
    now = time.time()
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise DaemonError("invalid_arguments", "A session names its account (sub)")
    exp = claims.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool) or not now < exp <= now + MAX_LIFETIME_S:
        raise DaemonError("invalid_arguments", "A session expires within 7 days (exp, whole seconds)")
    return auth_keys.sign_local(claims)
