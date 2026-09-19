import re
import subprocess

import pytest

from app.core.passwords import bcrypt_hash, bcrypt_verify, sha512_crypt_hash


def test_bcrypt_roundtrip():
    hashed = bcrypt_hash("s3cret-value")
    assert hashed.startswith("$2")
    assert bcrypt_verify("s3cret-value", hashed)
    assert not bcrypt_verify("other", hashed)


def test_bcrypt_verifies_a_known_reference_hash():
    # Public bcrypt test vector ($2a$ prefix, password "U*U"): hashes created by earlier releases stay valid.
    reference = "$2a$05$CCCCCCCCCCCCCCCCCCCCC.E5YPO9kmyuRGyh0XouQYb4YMJKvyOeW"
    assert bcrypt_verify("U*U", reference)
    assert not bcrypt_verify("U*V", reference)


def test_bcrypt_verify_rejects_malformed_hash():
    assert bcrypt_verify("x", "not-a-hash") is False


def test_bcrypt_long_password_is_truncated_not_rejected():
    long_password = "a" * 100
    assert bcrypt_verify(long_password, bcrypt_hash(long_password))


@pytest.mark.skipif(subprocess.run(["which", "openssl"], capture_output=True).returncode != 0, reason="openssl missing")
def test_sha512_crypt_is_verifiable_with_openssl():
    hashed = sha512_crypt_hash("Pa55word!")
    match = re.fullmatch(r"\$6\$([^$]+)\$.+", hashed)
    assert match
    again = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", match.group(1), "-stdin"],
        input="Pa55word!\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert again == hashed
