"""The HTTPS certificate of this node's web interface and API.

uvicorn reads `hyperlite.crt` and `hyperlite.key` from the TLS directory at start-up (installer/hyperlite.service);
scripts/ensure-tls-cert.sh writes a self-signed pair on the first boot. This module replaces that pair with an
imported certificate or one issued by Let's Encrypt (through certbot), and can go back to a self-signed one.

A new pair is checked the way uvicorn will load it (ssl.SSLContext.load_cert_chain) before it replaces the one in
use, the previous pair is kept next to it, and the service restarts outside its own cgroup so the answer to the
request that asked for it gets out first. A pair that would not load never reaches the service.
"""

import datetime
import os
import re
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.x509.oid import ExtensionOID, NameOID

TLS_DIR = Path(os.environ.get("HYPERLITE_TLS_DIR", "/root/hyperlite/data/tls"))
REPO_DIR = Path(__file__).resolve().parent.parent.parent
SELF_SIGNED_SCRIPT = REPO_DIR / "scripts" / "ensure-tls-cert.sh"
ACME_HOOK = REPO_DIR / "scripts" / "acme-deploy-hook.sh"
SOURCE_FILE = "source"  # "auto" (self-signed), "import" or "acme:<domain>"

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}$")
MAX_PEM = 64 * 1024


class CertError(ValueError):
    """A certificate or request refused, with a message for the user."""


def _paths():
    return TLS_DIR / "hyperlite.crt", TLS_DIR / "hyperlite.key"


def _source():
    try:
        return (TLS_DIR / SOURCE_FILE).read_text().strip() or "auto"
    except FileNotFoundError:
        return "auto"


def _describe(cert):
    try:
        sans = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        names = [str(v) for v in sans.get_values_for_type(x509.DNSName)] + [
            str(v) for v in sans.get_values_for_type(x509.IPAddress)
        ]
    except x509.ExtensionNotFound:
        names = []
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    not_after = cert.not_valid_after_utc
    return {
        "sujet": cn[0].value if cn else cert.subject.rfc4514_string(),
        "emetteur": cert.issuer.rfc4514_string(),
        "noms": names,
        "debut": cert.not_valid_before_utc.isoformat(),
        "fin": not_after.isoformat(),
        "jours_restants": (not_after - datetime.datetime.now(datetime.UTC)).days,
        "auto_signe": cert.issuer == cert.subject,
        "empreinte_sha256": cert.fingerprint(hashes.SHA256()).hex(":").upper(),
    }


def info():
    crt, _ = _paths()
    try:
        certs = x509.load_pem_x509_certificates(crt.read_bytes())
    except FileNotFoundError:
        return {"present": False, "source": _source(), "certbot": shutil.which("certbot") is not None}
    return {
        "present": True,
        "source": _source(),
        "certbot": shutil.which("certbot") is not None,
        "precedent": (TLS_DIR / "hyperlite.crt.previous").exists(),
        **_describe(certs[0]),
    }


def _load(cert_pem, key_pem):
    if len(cert_pem) > MAX_PEM or len(key_pem) > MAX_PEM:
        raise CertError("The certificate or key is too large for a PEM file")
    try:
        certs = x509.load_pem_x509_certificates(cert_pem.encode())
    except ValueError:
        raise CertError("The certificate is not a PEM certificate (-----BEGIN CERTIFICATE-----)") from None
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except TypeError:
        raise CertError("The private key is protected by a passphrase: import it without one") from None
    except ValueError:
        raise CertError("The private key is not a PEM private key (-----BEGIN ... PRIVATE KEY-----)") from None
    if not isinstance(key, rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey | ed25519.Ed25519PrivateKey):
        raise CertError("Unsupported key type: RSA, ECDSA or Ed25519 is expected")
    pub = serialization.PublicFormat.SubjectPublicKeyInfo
    if key.public_key().public_bytes(serialization.Encoding.DER, pub) != certs[0].public_key().public_bytes(
        serialization.Encoding.DER, pub
    ):
        raise CertError("The private key does not belong to the certificate (the first one of the file)")
    now = datetime.datetime.now(datetime.UTC)
    if certs[0].not_valid_after_utc <= now:
        raise CertError(f"The certificate expired on {certs[0].not_valid_after_utc:%Y-%m-%d}")
    if certs[0].not_valid_before_utc > now:
        raise CertError(f"The certificate is valid only from {certs[0].not_valid_before_utc:%Y-%m-%d}")
    return certs


def _check_loads(cert_bytes, key_bytes):
    """Loads the pair the way uvicorn does, from files, so a pair the service could not start with is refused."""
    with tempfile.TemporaryDirectory() as tmp:
        c, k = Path(tmp) / "c.pem", Path(tmp) / "k.pem"
        c.write_bytes(cert_bytes)
        k.write_bytes(key_bytes)
        k.chmod(0o600)
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(c, k)
        except ssl.SSLError as e:
            raise CertError(f"The certificate and key cannot be loaded together: {e.reason or e}") from None


def _write(path, data, mode):
    tmp = path.with_name(path.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def install(cert_bytes, key_bytes, source):
    """Put a checked pair in place, keeping the current one as `.previous`."""
    _check_loads(cert_bytes, key_bytes)
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TLS_DIR, 0o700)
    crt, key = _paths()
    if crt.exists() and key.exists():
        shutil.copy2(crt, crt.with_name("hyperlite.crt.previous"))
        shutil.copy2(key, key.with_name("hyperlite.key.previous"))
        os.chmod(key.with_name("hyperlite.key.previous"), 0o600)
    _write(key, key_bytes, 0o600)
    _write(crt, cert_bytes, 0o644)
    (TLS_DIR / SOURCE_FILE).write_text(source + "\n")


def import_pem(cert_pem, key_pem, chain_pem=""):
    """An administrator's certificate: the certificate (optionally followed by its chain), its key without
    passphrase, and an optional separate chain appended after it."""
    full = cert_pem.strip() + "\n" + (chain_pem.strip() + "\n" if chain_pem and chain_pem.strip() else "")
    certs = _load(full, key_pem.strip() + "\n")
    install(full.encode(), key_pem.strip().encode() + b"\n", "import")
    return _describe(certs[0])


def restore_previous():
    crt, key = _paths()
    prev_crt, prev_key = crt.with_name("hyperlite.crt.previous"), key.with_name("hyperlite.key.previous")
    if not (prev_crt.exists() and prev_key.exists()):
        raise CertError("There is no previous certificate to go back to")
    cert_bytes, key_bytes = prev_crt.read_bytes(), prev_key.read_bytes()
    install(cert_bytes, key_bytes, "import")


def self_signed():
    """A new self-signed pair, as on the first boot (scripts/ensure-tls-cert.sh writes it when none exists)."""
    crt, key = _paths()
    for p in (crt, key):
        if p.exists():
            shutil.copy2(p, p.with_name(p.name + ".previous"))
            p.unlink()
    subprocess.run([str(SELF_SIGNED_SCRIPT)], check=True, timeout=60, capture_output=True)
    (TLS_DIR / SOURCE_FILE).write_text("auto\n")


def acme_command(domain, email, staging=False):
    """certbot command for a Let's Encrypt certificate by HTTP-01 on port 80 (certbot answers the challenge
    itself); its deploy hook installs the pair and restarts the service, now and at each renewal of certbot's
    timer."""
    if not DOMAIN_RE.match(domain or ""):
        raise CertError("Invalid domain name (a public DNS name such as hv1.example.org is expected)")
    if not EMAIL_RE.match(email or ""):
        raise CertError("Invalid e-mail address")
    certbot = shutil.which("certbot")
    if certbot is None:
        raise CertError("certbot is not installed on this node: apt install certbot")
    cmd = [
        certbot,
        "certonly",
        "--standalone",
        "--non-interactive",
        "--agree-tos",
        "--keep-until-expiring",
        "--preferred-challenges",
        "http",
        "-d",
        domain,
        "-m",
        email,
        "--deploy-hook",
        str(ACME_HOOK),
    ]
    if staging:
        cmd.append("--test-cert")
    return cmd


def _install_files(cert_path, key_path, source):
    """Entry point of scripts/acme-deploy-hook.sh: install the pair certbot just wrote."""
    cert_bytes, key_bytes = Path(cert_path).read_bytes(), Path(key_path).read_bytes()
    _load(cert_bytes.decode(), key_bytes.decode())
    install(cert_bytes, key_bytes, source)


if __name__ == "__main__":
    import sys

    _install_files(sys.argv[1], sys.argv[2], sys.argv[3])
