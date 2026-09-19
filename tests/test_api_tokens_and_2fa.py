"""API tokens, TOTP two-factor codes and secret encryption."""

import pyotp

from app.core import api_tokens, secrets_crypto, twofa


def test_api_token_is_returned_once_and_verifies(database, make_user):
    make_user("alice")
    token_id, token = api_tokens.create_token("alice", "ci")
    assert isinstance(token_id, int)
    assert token.startswith(api_tokens.TOKEN_PREFIX)
    assert api_tokens.verify_token(token)["username"] == "alice"


def test_api_token_is_stored_hashed(database, make_user):
    make_user("alice")
    _, token = api_tokens.create_token("alice", "ci")
    with database.get_conn() as conn:
        stored = [dict(row) for row in conn.execute("SELECT * FROM api_tokens")]
    assert stored and all(token not in str(row.values()) for row in stored)


def test_unknown_or_malformed_api_tokens_are_rejected(database, make_user):
    make_user("alice")
    api_tokens.create_token("alice", "ci")
    assert api_tokens.verify_token("hlt_" + "0" * 40) is None
    assert api_tokens.verify_token("not-a-token") is None
    assert api_tokens.verify_token("") is None


def test_revoked_api_token_stops_working(database, make_user):
    make_user("alice")
    token_id, token = api_tokens.create_token("alice", "ci")
    assert api_tokens.revoke_token("alice", token_id) is True
    assert api_tokens.verify_token(token) is None


def test_a_user_cannot_revoke_someone_elses_token(database, make_user):
    make_user("alice")
    make_user("mallory")
    api_tokens.create_token("alice", "ci")
    token_id = api_tokens.list_tokens("alice")[0]["id"]
    assert api_tokens.revoke_token("mallory", token_id) is False
    assert len(api_tokens.list_tokens("alice")) == 1


def test_totp_code_round_trip():
    secret = twofa.generate_secret()
    assert twofa.verify_code(secret, pyotp.TOTP(secret).now())
    assert not twofa.verify_code(secret, "000000") or pyotp.TOTP(secret).now() == "000000"
    assert not twofa.verify_code(secret, "not-a-code")
    assert not twofa.verify_code(secret, "")


def test_totp_provisioning_uri_and_qr_code():
    secret = twofa.generate_secret()
    uri = twofa.provisioning_uri(secret, "alice")
    assert uri.startswith("otpauth://totp/") and "Hyperlite" in uri and "alice" in uri
    assert twofa.qr_code_svg(uri).lstrip().startswith(("<?xml", "<svg"))


def test_secret_encryption_round_trip():
    encrypted = secrets_crypto.encrypt("smtp-password")
    assert encrypted != "smtp-password"
    assert secrets_crypto.decrypt(encrypted) == "smtp-password"
    assert secrets_crypto.encrypt("smtp-password") != encrypted  # random IV: never deterministic


def test_legacy_plaintext_values_are_still_readable():
    assert secrets_crypto.decrypt("value-stored-before-encryption") == "value-stored-before-encryption"
    assert secrets_crypto.decrypt("") == ""
    assert secrets_crypto.encrypt("") == ""
