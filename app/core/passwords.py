"""Password hashing helpers.

- Hyperlite account passwords: bcrypt (direct use of the ``bcrypt`` package).
- Guest/container account passwords written into installer files: SHA-512 crypt
  (``$6$``), produced by ``openssl passwd -6`` because the stdlib ``crypt``
  module is gone from Python 3.13 and passlib is unmaintained.
"""

import subprocess

import bcrypt

_BCRYPT_MAX_BYTES = 72


def _prepare(password: str) -> bytes:
    # bcrypt only uses the first 72 bytes; newer bcrypt releases raise instead of truncating.
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def bcrypt_hash(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("ascii")


def bcrypt_verify(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(password), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def sha512_crypt_hash(password: str) -> str:
    """Return a ``$6$`` crypt hash (the password goes through stdin, never argv)."""
    result = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=password + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
