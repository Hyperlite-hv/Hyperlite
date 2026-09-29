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
from app.core.database import get_conn

logger = logging.getLogger(__name__)

ISSUER = "Hyperlite"


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
    with get_conn() as conn:
        # One conditional write: of two concurrent uses of the same code, only one updates the row.
        cur = conn.execute(
            "UPDATE users SET totp_last_step = ? WHERE username = ? AND (totp_last_step IS NULL OR totp_last_step < ?)",
            (step, username, step),
        )
        conn.commit()
    return cur.rowcount == 1


def encrypt_stored_secrets():
    """Encrypt the TOTP secrets stored in clear by earlier versions (run at start-up, idempotent)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT username, totp_secret FROM users WHERE totp_secret IS NOT NULL").fetchall()
        changed = 0
        for row in rows:
            if row["totp_secret"] and not secrets_crypto.is_encrypted(row["totp_secret"]):
                conn.execute(
                    "UPDATE users SET totp_secret = ? WHERE username = ?",
                    (seal_secret(row["totp_secret"]), row["username"]),
                )
                changed += 1
        conn.commit()
    if changed:
        logger.info("Encrypted %d TOTP secret(s) stored in clear", changed)
