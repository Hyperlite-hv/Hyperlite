"""OIDC single sign-on: authentication delegated to an external identity
provider (IdP) through OpenID Connect (Authorization Code flow), IN ADDITION to
the existing local authentication and never in place of it. The local admin
account must stay usable as a fallback when the IdP is unreachable or
misconfigured; otherwise a bad SSO configuration would lock the whole server.

No new dependency: urllib (standard library, the same convention as
app/core/notifications.py) for HTTP calls to the IdP, and PyJWT (already used by
app/core/security.py for Hyperlite session JWTs) to validate the signature of
the ID token returned by the IdP.

Role mapping: the role model of this project is BINARY at the global level
(`users.role` is only 'admin' or 'observateur', see app/core/database.py;
fine-grained access goes through the ACLs, groups and custom roles of
app/core/permissions.py, not through this global role). An SSO account gets
'admin' if one of its IdP groups (configurable claim name, default "groups")
is in the "admin groups" list configured in Hyperlite, and 'observateur' (the
most restrictive) otherwise. The role is RE-RESOLVED on EVERY SSO login, not
only when the account is created: a user removed from the admin group on the
IdP side loses Hyperlite admin rights at the next login instead of staying an
admin locally indefinitely.

"""

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

import jwt

from app.core import secrets_crypto
from app.core.database import get_conn
from app.core.http_safety import require_http_url
from app.core.security import hash_password

STATE_TTL_S = 600  # 10 min: enough time to authenticate at the IdP, no more
HANDOFF_TTL_S = 60  # from the callback to the dashboard asking for its session: a redirect, a page load
HTTP_TIMEOUT_S = 10


class LocalAccountConflict(Exception):
    """Raised when an SSO login resolves to a username that ALREADY belongs to a
    local account (auth_source != 'sso'). It is refused instead of silently
    overwriting that account's role or status, which could otherwise strip
    admin rights from the local fallback account."""


def get_config():
    """Decrypt client_secret for INTERNAL use (code exchange with the IdP,
    exchange_code() below). app/routers/sso.py NEVER returns this field as is
    to a client (see GET /config, which replaces it with
    client_secret_set: bool)."""
    with get_conn() as db:
        row = db.execute("SELECT * FROM sso_config WHERE id = 1").fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("client_secret"):
            try:
                d["client_secret"] = secrets_crypto.decrypt(d["client_secret"])
            except secrets_crypto.SecretUnreadable:
                d["client_secret"] = None
                d["client_secret_unreadable"] = True
        return d


# Columns that set_config() may write. Column names are interpolated into the
# SQL text (values are always bound), so they must never come from input that
# has not been checked against this list.
CONFIG_COLUMNS = frozenset(
    {
        "enabled",
        "issuer",
        "client_id",
        "client_secret",
        "redirect_uri",
        "scope",
        "group_claim",
        "admin_groups",
    }
)


def set_config(**fields):
    if not fields:
        return
    unknown = set(fields) - CONFIG_COLUMNS
    if unknown:
        raise ValueError(f"Unknown SSO configuration field(s): {', '.join(sorted(unknown))}")
    if fields.get("client_secret"):
        fields["client_secret"] = secrets_crypto.encrypt(fields["client_secret"])
    with get_conn() as db:
        existing = db.execute("SELECT id FROM sso_config WHERE id = 1").fetchone()
        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            db.execute(f"UPDATE sso_config SET {sets} WHERE id = 1", list(fields.values()))  # noqa: S608 -- only fixed fragments/allowlisted column names are interpolated; values are bound parameters
        else:
            cols = ["id", *list(fields.keys())]
            placeholders = ", ".join("?" for _ in cols)
            # Only allowlisted column names are interpolated; values are bound parameters.
            db.execute(
                f"INSERT INTO sso_config ({', '.join(cols)}) VALUES ({placeholders})",  # noqa: S608
                [1, *list(fields.values())],
            )
        db.commit()


def _http_get_json(url):
    require_http_url(url)
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310 -- scheme validated above
        return json.loads(resp.read().decode("utf-8"))


def _http_post_form(url, data):
    require_http_url(url)
    body = urlencode(data).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 -- URL scheme validated by require_http_url() or a constant https URL
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310 -- URL scheme validated by require_http_url() or a constant https URL
        return json.loads(resp.read().decode("utf-8"))


def discover(issuer):
    """Standard OIDC discovery document: a single field (the issuer) to enter on the
    admin side instead of 4 separate URLs (authorization/token/jwks/userinfo
    endpoints), exactly what the OpenID Connect Discovery 1.0 specification
    provides."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    return _http_get_json(url)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def create_state():
    """(state, nonce, binding). `binding` goes to the browser starting the sign-in, in an HttpOnly cookie; only its
    hash is stored. The callback is accepted only from that browser: otherwise anyone could start a sign-in with
    their own account and send its callback link to a victim, whose browser then ended up signed in to the
    attacker's account (login CSRF)."""
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    binding = secrets.token_urlsafe(32)
    now = time.time()
    with get_conn() as db:
        # Purge expired states along the way: such a short-lived table (10 minute
        # lifetime) does not justify a dedicated scheduler.
        db.execute("DELETE FROM sso_login_state WHERE created_at < ?", (now - STATE_TTL_S,))
        db.execute(
            "INSERT INTO sso_login_state (state, nonce, created_at, binding) VALUES (?, ?, ?, ?)",
            (state, nonce, now, _digest(binding)),
        )
        db.commit()
    return state, nonce, binding


def consume_state(state, binding):
    """The nonce of `state` if it is valid and comes back to the browser that started it, else None. SINGLE USE:
    the row is deleted as soon as it is read (standard replay protection of the Authorization Code flow: a
    `state` must never be usable twice)."""
    with get_conn() as db:
        row = db.execute("SELECT nonce, created_at, binding FROM sso_login_state WHERE state = ?", (state,)).fetchone()
        if not row:
            return None
        db.execute("DELETE FROM sso_login_state WHERE state = ?", (state,))
        db.commit()
    if time.time() - row["created_at"] > STATE_TTL_S:
        return None
    if not binding or not row["binding"] or not hmac.compare_digest(row["binding"], _digest(binding)):
        return None
    return row["nonce"]


def create_handoff(username):
    """A one-time code handing a completed SSO sign-in over to the dashboard. The session token used to travel in
    the redirect URL (?sso_token=), where it stayed in the browser history and in the access logs of the server
    and of any proxy."""
    code = secrets.token_urlsafe(32)
    now = time.time()
    with get_conn() as db:
        db.execute("DELETE FROM sso_handoffs WHERE created_at < ?", (now - HANDOFF_TTL_S,))
        db.execute(
            "INSERT INTO sso_handoffs (code, username, created_at) VALUES (?, ?, ?)", (_digest(code), username, now)
        )
        db.commit()
    return code


def consume_handoff(code):
    """The username a handoff code was issued for, once, within HANDOFF_TTL_S; else None."""
    if not code:
        return None
    with get_conn() as db:
        row = db.execute("SELECT username, created_at FROM sso_handoffs WHERE code = ?", (_digest(code),)).fetchone()
        if not row:
            return None
        db.execute("DELETE FROM sso_handoffs WHERE code = ?", (_digest(code),))
        db.commit()
    if time.time() - row["created_at"] > HANDOFF_TTL_S:
        return None
    return row["username"]


def build_authorize_url(config, discovery_doc, state, nonce):
    params = {
        "response_type": "code",
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "scope": config["scope"],
        "state": state,
        "nonce": nonce,
    }
    return discovery_doc["authorization_endpoint"] + "?" + urlencode(params)


def exchange_code(config, discovery_doc, code):
    return _http_post_form(
        discovery_doc["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        },
    )


# Only asymmetric signature algorithms are accepted for ID tokens. The `alg`
# header is attacker-controlled: allowing HS* would let a forged token be
# "signed" with the IdP's public key used as an HMAC secret (algorithm
# confusion), and `none` would disable verification entirely.
ID_TOKEN_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
ID_TOKEN_LEEWAY_S = 60  # tolerated clock skew between Hyperlite and the IdP


def validate_id_token(config, discovery_doc, id_token, nonce):
    """Verify an OIDC ID token: the signature (against the IdP JWKS, key
    selected by `kid`), the standard claims (issuer, audience, expiry) and the
    nonce (replay protection specific to the ID token, distinct from the
    `state` that protects the redirect itself). Raises jwt.PyJWTError on any
    failure."""
    jwks = _http_get_json(discovery_doc["jwks_uri"])
    header = jwt.get_unverified_header(id_token)
    alg = header.get("alg")
    if alg not in ID_TOKEN_ALGORITHMS:
        raise jwt.InvalidAlgorithmError(f"Unsupported ID token algorithm: {alg!r}")
    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
    if key_data is None:
        raise jwt.InvalidKeyError("Unknown signing key (kid not present in the IdP JWKS)")
    key = jwt.PyJWK.from_dict(key_data).key
    claims = jwt.decode(
        id_token,
        key,
        algorithms=[alg],
        audience=config["client_id"],
        issuer=config["issuer"],
        leeway=ID_TOKEN_LEEWAY_S,
        options={"require": ["exp", "iss", "aud"]},
    )
    if claims.get("nonce") != nonce:
        raise jwt.InvalidTokenError("Invalid nonce (possible replay)")
    return claims


def resolve_role(config, claims):
    admin_groups = {g.strip() for g in (config.get("admin_groups") or "").split(",") if g.strip()}
    if not admin_groups:
        return "observateur"
    user_groups = set(claims.get(config.get("group_claim") or "groups") or [])
    return "admin" if user_groups & admin_groups else "observateur"


def resolve_username(claims):
    return claims.get("preferred_username") or claims.get("email") or claims.get("sub")


class SubjectConflict(Exception):
    """The username the IdP proposes already belongs to another SSO identity (another `sub`)."""


def provision_user(username, role, subject=None):
    """Create the SSO account if it does not exist yet, or update its role on EVERY
    login (see the module docstring). The local password is made structurally
    unusable (a random hashed secret, never disclosed anywhere): this account
    can only authenticate through SSO. Raises LocalAccountConflict if the name
    already belongs to a local account: NEVER silently overwrite an existing
    non-SSO account (see the exception docstring).

    `subject`: the IdP's `sub`, the only claim that is stable and unique per identity (a preferred_username or an
    email can be changed, or reused by another person). The account is found by it first, and a username already
    bound to another subject is refused instead of letting a second identity into that account. Accounts created
    before this was recorded are bound to the first subject that signs in to them."""
    with get_conn() as db:
        if subject:
            bound = db.execute(
                "SELECT username FROM users WHERE auth_source = 'sso' AND sso_subject = ?", (subject,)
            ).fetchone()
            if bound is not None:
                username = bound["username"]
        existing = db.execute(
            "SELECT username, role, auth_source, sso_subject FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing is not None and existing["auth_source"] != "sso":
            raise LocalAccountConflict(username)
        if existing is not None and subject and existing["sso_subject"] and existing["sso_subject"] != subject:
            raise SubjectConflict(username)
        if existing is None:
            db.execute(
                "INSERT INTO users (username, hashed_password, role, auth_source, sso_subject) VALUES (?, ?, ?, 'sso', ?)",
                (username, hash_password(secrets.token_hex(32)), role, subject),
            )
        else:
            db.execute(
                "UPDATE users SET role = ?, sso_subject = COALESCE(sso_subject, ?) WHERE username = ?",
                (role, subject, username),
            )
        db.commit()
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row)
