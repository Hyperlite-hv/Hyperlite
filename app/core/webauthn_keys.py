"""Security keys and passkeys (WebAuthn) as a second factor: a YubiKey, Windows Hello, Touch ID, or a phone.

A user registers keys from their account; at sign-in, after the password, a registered key answers instead of the
TOTP code (either one works when both are set up). The py_webauthn library checks the signatures, the challenge,
the origin and the relying party; this module stores the keys and the challenges.

Relying party (RP) ID: WebAuthn binds a key to the host name the dashboard is opened with (hyperlite.example.lan).
Hyperlite has no configured public name, so the RP ID is the host of the browser's Origin at registration, stored
with the key; at sign-in only the keys registered for that same host are offered and accepted. A key registered
through one name therefore does not work through another. Browsers refuse WebAuthn on an IP address and outside
HTTPS (except on localhost): the request is refused up front with that explanation.

Challenges are random, single-use, bound to the user, the purpose and the origin, and expire after two minutes.
"""

import ipaddress
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import webauthn
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.database import get_conn

logger = logging.getLogger(__name__)

RP_NAME = "Hyperlite"
CHALLENGE_TTL_S = 120
MAX_KEYS_PER_USER = 20
REGISTER = "register"
LOGIN = "login"


class WebAuthnError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _now():
    return datetime.now(UTC)


def relying_party(origin):
    """(rp_id, origin) from the browser's Origin header, or WebAuthnError when a browser would refuse WebAuthn."""
    if not origin:
        raise WebAuthnError("The browser sent no Origin: security keys need the dashboard opened in a browser")
    parts = urlsplit(origin)
    host = (parts.hostname or "").lower()
    if not host or parts.scheme not in ("https", "http"):
        raise WebAuthnError("Unrecognised origin")
    try:
        ipaddress.ip_address(host)
        raise WebAuthnError(
            "Security keys do not work on an IP address: open the dashboard through its host name (HTTPS)"
        )
    except ValueError:
        pass
    if parts.scheme != "https" and host != "localhost":
        raise WebAuthnError("Security keys need the dashboard opened over HTTPS")
    return host, f"{parts.scheme}://{parts.netloc}"


# --- Storage ----------------------------------------------------------------------------------------------------


def list_keys(username):
    with get_conn() as db:
        rows = db.execute(
            "SELECT id, nom, rp_id, cree_le, utilise_le FROM webauthn_credentials WHERE username = ? ORDER BY cree_le",
            (username,),
        ).fetchall()
    return [dict(r) for r in rows]


def has_keys(username):
    with get_conn() as db:
        return (
            db.execute("SELECT 1 FROM webauthn_credentials WHERE username = ? LIMIT 1", (username,)).fetchone()
            is not None
        )


def delete_key(username, key_id):
    with get_conn() as db:
        cur = db.execute("DELETE FROM webauthn_credentials WHERE id = ? AND username = ?", (key_id, username))
        db.commit()
        return cur.rowcount > 0


def delete_all_keys(username):
    with get_conn() as db:
        db.execute("DELETE FROM webauthn_credentials WHERE username = ?", (username,))
        db.commit()


def _store_challenge(username, purpose, challenge, rp_id, origin):
    with get_conn() as db:
        db.execute("DELETE FROM webauthn_challenges WHERE expire_le < ?", (_now().isoformat(),))
        db.execute(
            "INSERT INTO webauthn_challenges (username, but, challenge, rp_id, origin, expire_le) VALUES (?, ?, ?, ?, ?, ?)",
            (
                username,
                purpose,
                bytes_to_base64url(challenge),
                rp_id,
                origin,
                (_now() + timedelta(seconds=CHALLENGE_TTL_S)).isoformat(),
            ),
        )
        db.commit()


def _take_challenge(username, purpose, rp_id, origin, client_data_challenge):
    """The stored challenge the browser signed, consumed (single use). None when unknown or expired."""
    with get_conn() as db:
        row = db.execute(
            "SELECT id, expire_le FROM webauthn_challenges WHERE username = ? AND but = ? AND challenge = ? "
            "AND rp_id = ? AND origin = ?",
            (username, purpose, client_data_challenge, rp_id, origin),
        ).fetchone()
        if row is None:
            return None
        db.execute("DELETE FROM webauthn_challenges WHERE id = ?", (row["id"],))
        db.commit()
    if row["expire_le"] < _now().isoformat():
        return None
    return webauthn.base64url_to_bytes(client_data_challenge)


def _client_challenge(credential):
    """The challenge inside the browser's clientDataJSON (checked again by py_webauthn against the stored one)."""
    try:
        client_data = json.loads(webauthn.base64url_to_bytes(credential["response"]["clientDataJSON"]))
        return str(client_data["challenge"])
    except (KeyError, TypeError, ValueError):
        raise WebAuthnError("Malformed security key answer") from None


# --- Registration -----------------------------------------------------------------------------------------------


def registration_options(username, origin):
    rp_id, origin = relying_party(origin)
    existing = list_keys(username)
    if len(existing) >= MAX_KEYS_PER_USER:
        raise WebAuthnError(f"At most {MAX_KEYS_PER_USER} security keys per account")
    with get_conn() as db:
        exclude = [
            PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(r["credential_id"]))
            for r in db.execute(
                "SELECT credential_id FROM webauthn_credentials WHERE username = ? AND rp_id = ?", (username, rp_id)
            )
        ]
    challenge = secrets.token_bytes(32)
    options = webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_name=username,
        user_id=username.encode(),
        challenge=challenge,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED, user_verification=UserVerificationRequirement.PREFERRED
        ),
    )
    _store_challenge(username, REGISTER, challenge, rp_id, origin)
    return json.loads(webauthn.options_to_json(options))


def register(username, origin, credential, name):
    rp_id, origin = relying_party(origin)
    name = (name or "").strip()[:64] or "Security key"
    challenge = _take_challenge(username, REGISTER, rp_id, origin, _client_challenge(credential))
    if challenge is None:
        raise WebAuthnError("The registration expired or was already used: start again")
    try:
        verified = webauthn.verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin
        )
    except (InvalidRegistrationResponse, ValueError, KeyError, TypeError) as e:
        raise WebAuthnError(f"The security key answer was refused: {e}") from None
    credential_id = bytes_to_base64url(verified.credential_id)
    with get_conn() as db:
        if db.execute("SELECT 1 FROM webauthn_credentials WHERE credential_id = ?", (credential_id,)).fetchone():
            raise WebAuthnError("This security key is already registered", 409)
        cur = db.execute(
            "INSERT INTO webauthn_credentials (username, nom, credential_id, public_key, sign_count, rp_id, cree_le) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                username,
                name,
                credential_id,
                bytes_to_base64url(verified.credential_public_key),
                verified.sign_count,
                rp_id,
                _now().isoformat(),
            ),
        )
        db.commit()
        return cur.lastrowid


# --- Sign-in ----------------------------------------------------------------------------------------------------


def authentication_options(username, origin):
    rp_id, origin = relying_party(origin)
    with get_conn() as db:
        rows = db.execute(
            "SELECT credential_id FROM webauthn_credentials WHERE username = ? AND rp_id = ?", (username, rp_id)
        ).fetchall()
    if not rows:
        raise WebAuthnError(
            f"No security key of this account is registered for {rp_id}: use the code, or the address the key was added with"
        )
    challenge = secrets.token_bytes(32)
    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=webauthn.base64url_to_bytes(r["credential_id"])) for r in rows
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _store_challenge(username, LOGIN, challenge, rp_id, origin)
    return json.loads(webauthn.options_to_json(options))


def authenticate(username, origin, credential):
    """True when a registered key of this user signed this sign-in's challenge. Updates its counter and last use."""
    rp_id, origin = relying_party(origin)
    challenge = _take_challenge(username, LOGIN, rp_id, origin, _client_challenge(credential))
    if challenge is None:
        return False
    credential_id = str(credential.get("id") or "")
    with get_conn() as db:
        row = db.execute(
            "SELECT id, public_key, sign_count FROM webauthn_credentials WHERE username = ? AND credential_id = ? AND rp_id = ?",
            (username, credential_id, rp_id),
        ).fetchone()
    if row is None:
        return False
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=webauthn.base64url_to_bytes(row["public_key"]),
            credential_current_sign_count=row["sign_count"],
        )
    except (InvalidAuthenticationResponse, ValueError, KeyError, TypeError):
        logger.info("Security key sign-in refused for %s", username, exc_info=True)
        return False
    with get_conn() as db:
        db.execute(
            "UPDATE webauthn_credentials SET sign_count = ?, utilise_le = ? WHERE id = ?",
            (verified.new_sign_count, _now().isoformat(), row["id"]),
        )
        db.commit()
    return True
