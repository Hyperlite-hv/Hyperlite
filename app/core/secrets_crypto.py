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

import fcntl
import logging
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_KEY_VAR = "HYPERLITE_ENCRYPTION_KEY"
_fernet = None
_key_lock = threading.Lock()

# Every Fernet token starts with the base64 of its version byte (0x80): what tells an encrypted value from a
# plaintext one stored before encryption existed.
_FERNET_PREFIX = "gAAAAA"


class SecretUnreadable(RuntimeError):
    """An encrypted secret that the current key cannot decrypt (the key changed or was lost: a restored .env, a
    configuration copied from another node). The secret must be entered again."""


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
    # Two first uses at once (two threads, or the service and a script) must not each
    # append a key of their own: the file is locked, and read again under the lock.
    with _key_lock, open(ENV_PATH, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            key = _read_key_from_env_file()
            if key:
                os.environ[_KEY_VAR] = key
                return key.encode()
            new_key = Fernet.generate_key()
            f.write(f"\n{_KEY_VAR}={new_key.decode()}\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
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


def is_encrypted(value):
    return bool(value) and value.startswith(_FERNET_PREFIX)


def decrypt(value):
    """The plaintext of a stored secret.

    A value stored before encryption existed is not a Fernet token: it is returned as is and keeps working until
    an admin next edits it (which then encrypts it). An ENCRYPTED value that does not decrypt raises
    SecretUnreadable: handing the ciphertext on as if it were the secret only produced an opaque refusal from the
    SMTP server or the identity provider, with nothing saying why."""
    if not value:
        return value
    if not is_encrypted(value):
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except Exception as e:
        logger.error("A stored secret cannot be decrypted with the current %s", _KEY_VAR)
        raise SecretUnreadable(
            "A stored secret cannot be decrypted: the encryption key changed since it was saved. Enter it again."
        ) from e
