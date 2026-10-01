"""Two-factor authentication (TOTP). pyotp generates and verifies the codes;
qrcode renders the QR code directly as SVG (SvgPathImage: a single <path>,
no Pillow dependency, see requirements.txt)."""

import hmac
import io
import logging
import time

import pyotp
import qrcode
import qrcode.image.svg

from app.core import secrets_crypto

logger = logging.getLogger(__name__)

ISSUER = "Hyperlite"


def _accounts():
    # The account repository's synchronous bridge: signing in runs in FastAPI dependencies and sync endpoints.
    from app.repositories import registry

    return registry.accounts().sync


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def qr_code_svg(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def seal_secret(secret: str) -> str:
    """The form a TOTP secret is stored in: encrypted like the other secrets of the database. In clear, a leaked
    hyperlite.db (the case secrets_crypto exists for) handed out every account's second factor."""
    return secrets_crypto.encrypt(secret)


def _matching_step(secret: str, code: str, now: float):
    """The time step whose code is `code`, within +-1 step (+-30 s of clock drift between the server and the phone,
    a common real-world case), or None."""
    totp = pyotp.TOTP(secret)
    code = (code or "").strip()
    if not code.isdigit():
        return None
    current = int(now // totp.interval)
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def verify_code(username: str, stored_secret: str, code: str) -> bool:
    """Check a TOTP code for an account, and accept each code ONCE: the step it belongs to must be later than the
    last accepted one. Without this, a code seen over a shoulder or intercepted stayed valid for about 90 s."""
    if not stored_secret or not code:
        return False
    try:
        secret = secrets_crypto.decrypt(stored_secret)
        step = _matching_step(secret, code, time.time())
    except secrets_crypto.SecretUnreadable:
        logger.error("The TOTP secret of %s cannot be decrypted", username)
        return False
    except Exception:
        return False
    if step is None:
        return False
    # One conditional write: of two concurrent uses of the same code, only one updates the row.
    return _accounts().claim_totp_step(username, step)


def encrypt_stored_secrets():
    """Encrypt the TOTP secrets stored in clear by earlier versions (run at start-up, idempotent)."""
    changed = 0
    for row in _accounts().totp_secrets():
        if row["totp_secret"] and not secrets_crypto.is_encrypted(row["totp_secret"]):
            _accounts().replace_totp_secret(row["username"], seal_secret(row["totp_secret"]))
            changed += 1
    if changed:
        logger.info("Encrypted %d TOTP secret(s) stored in clear", changed)
