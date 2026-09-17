"""2FA (TOTP) -- chantier 30 (2026-09-17). pyotp genere/verifie les codes,
qrcode produit le QR code directement en SVG (SvgPathImage : un seul
<path>, pas de dependance Pillow -- voir requirements.txt)."""
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
    """valid_window=1 : tolere +-30s de derive d'horloge entre le serveur et
    l'appareil qui genere le code, cas reel frequent (pas de sync NTP
    garantie sur un telephone)."""
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False
