"""Encryption at rest for secrets stored in the database (the SMTP password
and the OIDC client secret).

Honest limitation: the encryption key lives in the SAME `.env` file as the
rest of the configuration, on the SAME disk as `hyperlite.db`. An attacker
with FULL access to the filesystem is NOT stopped by this encryption (the
project has no external vault or KMS). It does provide real protection against
a narrower but realistic scenario: a leak of the `hyperlite.db` file alone (a
backup copied or shared without its `.env`, a limited SQL extraction, a
partial dump).

Fernet (from the `cryptography` package, already a dependency and used
elsewhere for PyJWT in security.py and sso.py): AES-128 in CBC mode plus
HMAC-SHA256, authenticated encryption, a proven and simple format rather than
a home-made scheme.

"""

import os
from pathlib import Path

from cryptography.fernet import Fernet

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_KEY_VAR = "HYPERLITE_ENCRYPTION_KEY"
_fernet = None


def _read_key_from_env_file():
    """The service runs under systemd (EnvironmentFile=.env, see
    hyperlite.service), so os.environ always contains the key for it. A script
    started by hand (outside systemd, e.g. a test) does NOT inherit it
    automatically even when .env already holds the variable: such a script
    once generated a SECOND key and appended it to .env (it only looked at
    os.environ, never at the file), making secrets already encrypted with the
    first key unreadable. Fixed by reading the file directly as a last resort,
    BEFORE concluding that no key exists yet."""
    if not ENV_PATH.exists():
        return None
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{_KEY_VAR}="):
            return line.split("=", 1)[1].strip()
    return None


def _load_or_create_key():
    key = os.environ.get(_KEY_VAR) or _read_key_from_env_file()
    if key:
        os.environ[_KEY_VAR] = key
        return key.encode()
    # No key anywhere (first time this machine needs one): generate one and PERSIST
    # it in .env immediately. A key lost at the next restart would make every
    # already encrypted secret unreadable forever (there is no other copy).
    new_key = Fernet.generate_key()
    with open(ENV_PATH, "a") as f:
        f.write(f"\n{_KEY_VAR}={new_key.decode()}\n")
    os.chmod(ENV_PATH, 0o600)
    os.environ[_KEY_VAR] = new_key.decode()
    return new_key


def _get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext):
    if not plaintext:
        return plaintext
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(value):
    """Silently fall back to the value as is if decryption fails. This covers
    values stored before encryption existed (never encrypted, so not a valid
    Fernet token): rather than crashing an existing SMTP send or OIDC login,
    the old plaintext value keeps working until an admin next edits it (which
    then encrypts it)."""
    if not value:
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except Exception:
        return value
