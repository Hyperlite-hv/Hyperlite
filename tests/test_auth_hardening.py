"""Sessions and tokens: what a password change, a sign-out, an API token or an SSO sign-in can and cannot do."""

import time

import pytest

from app.core import api_tokens, client_address, secrets_crypto, security, sso

PASSWORD = "correct horse battery"


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _session(client, make_user, username, role="admin"):
    make_user(username, role, PASSWORD)
    r = client.post("/auth/login", data={"username": username, "password": PASSWORD})
    return r.json()["access_token"]


def test_changing_ones_password_revokes_every_api_token(client, make_user):
    token = _session(client, make_user, "ann")
    api = client.post("/auth/tokens", headers=_bearer(token), json={"name": "ci"}).json()["token"]
    assert client.get("/auth/me", headers=_bearer(api)).status_code == 200
    r = client.post(
        "/auth/me/password",
        headers=_bearer(token),
        json={"current_password": PASSWORD, "new_password": "a much longer passphrase 2"},
    )
    assert r.status_code == 200 and r.json()["revoked_tokens"] == 1
    assert client.get("/auth/me", headers=_bearer(api)).status_code == 401


def test_a_token_made_by_hand_expires_unless_asked_otherwise(client, make_user):
    token = _session(client, make_user, "ben")
    made = client.post("/auth/tokens", headers=_bearer(token), json={"name": "ci"}).json()
    assert made["expires_at"] is not None
    forever = client.post("/auth/tokens", headers=_bearer(token), json={"name": "cron", "expires_days": None}).json()
    assert forever["expires_at"] is None


def test_an_api_token_cannot_mint_tokens_nor_revoke_others(client, make_user):
    token = _session(client, make_user, "cid")
    one = client.post("/auth/tokens", headers=_bearer(token), json={"name": "one"}).json()
    two = client.post("/auth/tokens", headers=_bearer(token), json={"name": "two"}).json()
    leaked = _bearer(one["token"])
    assert client.post("/auth/tokens", headers=leaked, json={"name": "backdoor"}).status_code == 403
    assert client.delete(f"/auth/tokens/{two['id']}", headers=leaked).status_code == 403
    assert client.delete(f"/auth/tokens/{one['id']}", headers=leaked).status_code == 200  # a workstation signing out


def test_a_forced_password_change_also_binds_api_tokens(client, make_user, database):
    token = _session(client, make_user, "dee")
    api = client.post("/auth/tokens", headers=_bearer(token), json={"name": "ci"}).json()["token"]
    security.flag_weak_password("dee")
    assert client.get("/vms", headers=_bearer(api)).status_code == 403
    assert client.get("/auth/me", headers=_bearer(api)).status_code == 200  # who am I stays open


def test_signing_out_revokes_the_session_everywhere(client, make_user):
    token = _session(client, make_user, "eli")
    assert client.post("/auth/logout", headers=_bearer(token)).status_code == 200
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401


def test_an_unknown_username_costs_a_password_check_too(database, monkeypatch):
    checked = []
    monkeypatch.setattr(security, "verify_password", lambda plain, hashed: checked.append(hashed) or False)
    assert security.authenticate_user("nobody", "whatever") is None
    assert len(checked) == 1


def test_an_empty_or_short_session_key_is_refused(monkeypatch):
    monkeypatch.setenv("HYPERLITE_SECRET_KEY", "")
    with pytest.raises(RuntimeError, match="at least 32"):
        security._load_secret_key()
    monkeypatch.setenv("HYPERLITE_SECRET_KEY", "short")
    with pytest.raises(RuntimeError):
        security._load_secret_key()
    monkeypatch.delenv("HYPERLITE_SECRET_KEY")
    assert len(security._load_secret_key()) > 32  # development: random, with a warning


def test_an_encrypted_secret_that_does_not_decrypt_is_an_error_not_a_password(monkeypatch):
    from cryptography.fernet import Fernet

    sealed = Fernet(Fernet.generate_key()).encrypt(b"s3cret").decode()  # another key
    with pytest.raises(secrets_crypto.SecretUnreadable):
        secrets_crypto.decrypt(sealed)
    assert secrets_crypto.decrypt("legacy clear value") == "legacy clear value"
    assert secrets_crypto.decrypt(secrets_crypto.encrypt("ok")) == "ok"


@pytest.mark.parametrize(
    ("peer", "forwarded", "trusted", "expected"),
    [
        ("198.51.100.1", "203.0.113.9", "", "198.51.100.1"),  # untrusted peer: header ignored
        ("10.0.0.2", "203.0.113.9", "10.0.0.2", "203.0.113.9"),
        ("10.0.0.2", "1.2.3.4, 203.0.113.9, 10.0.0.3", "10.0.0.2,10.0.0.3", "203.0.113.9"),  # forged left part
        ("10.0.0.2", "garbage", "10.0.0.2", "10.0.0.2"),
    ],
)
def test_the_client_address_trusts_only_configured_proxies(peer, forwarded, trusted, expected):
    assert client_address.resolve(peer, forwarded, client_address._parse(trusted)) == expected


# --- SSO ---


def test_an_sso_callback_is_accepted_only_from_the_browser_that_started_it(database):
    state, nonce, binding = sso.create_state()
    assert sso.consume_state(state, "someone-elses-cookie") is None
    state, nonce, binding = sso.create_state()
    assert sso.consume_state(state, binding) == nonce
    assert sso.consume_state(state, binding) is None  # single use


def test_the_sso_handoff_is_single_use_and_short_lived(database, monkeypatch):
    code = sso.create_handoff("fay")
    assert sso.consume_handoff(code) == "fay"
    assert sso.consume_handoff(code) is None
    old = sso.create_handoff("fay")
    real = time.time
    monkeypatch.setattr(sso.time, "time", lambda: real() + sso.HANDOFF_TTL_S + 1)
    assert sso.consume_handoff(old) is None


def test_the_exchange_gives_a_session_or_asks_for_the_second_factor(client, make_user, database):
    make_user("gia", "observateur", PASSWORD)
    client.cookies.set("hl_sso_handoff", sso.create_handoff("gia"), path="/auth/sso")
    r = client.post("/auth/sso/exchange")
    assert r.status_code == 200 and r.json()["access_token"] and r.json()["username"] == "gia"
    assert client.post("/auth/sso/exchange").status_code == 401  # the code is spent

    with database.get_conn() as conn:
        conn.execute("UPDATE users SET totp_enabled = 1, totp_secret = 'x' WHERE username = 'gia'")
        conn.commit()
    client.cookies.set("hl_sso_handoff", sso.create_handoff("gia"), path="/auth/sso")
    body = client.post("/auth/sso/exchange").json()
    assert body["require_2fa"] is True and "access_token" not in body


def test_an_sso_account_is_bound_to_its_subject(database):
    first = sso.provision_user("hal", "observateur", subject="sub-1")
    assert first["sso_subject"] == "sub-1"
    with pytest.raises(sso.SubjectConflict):
        sso.provision_user("hal", "observateur", subject="sub-2")  # another identity claiming the same name
    renamed = sso.provision_user("hal-renamed", "admin", subject="sub-1")  # the IdP renamed the user
    assert renamed["username"] == "hal" and renamed["role"] == "admin"


def test_api_tokens_module_reports_which_token_authenticated(database, make_user):
    make_user("ivy")
    token_id, token = api_tokens.create_token("ivy", "ci")
    assert api_tokens.verify_token(token)["api_token_id"] == token_id
