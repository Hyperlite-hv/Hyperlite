"""Two-factor authentication (TOTP). pyotp generates and verifies the codes;
qrcode renders the QR code directly as SVG (SvgPathImage: a single <path>,
no Pillow dependency, see requirements.txt)."""

import io

import pyotp
import qrcode
import qrcode.image.svg

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


def verify_code(secret: str, code: str) -> bool:
    """valid_window=1 tolerates +-30 s of clock drift between the server and the
    device generating the code, a common real-world case (a phone has no
    guaranteed NTP sync)."""
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False
