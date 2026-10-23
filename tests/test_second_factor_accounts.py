"""Second factors on SSO and directory accounts (GHSA-7wjv-ggfp-7x9c): a session alone never enrols one, the owner
can always remove theirs, and an administrator can reset them."""

import time

import pyotp

PASSWORD = "correct horse battery"


def _source(database, username, source):
    with database.get_conn() as conn:
        conn.execute("UPDATE users SET auth_source = ? WHERE username = ?", (source, username))
        conn.commit()


def _enrol(client, session, password=PASSWORD):
    secret = client.post("/auth/2fa/setup", headers=session, json={"password": password}).json()["secret"]
    assert client.post("/auth/2fa/confirm", headers=session, json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    return secret


def _later_code(secret):
    return pyotp.TOTP(secret).at(time.time() + 30)  # the one confirming the setup is spent


def test_an_sso_session_alone_enrols_no_second_factor(client, auth_headers, database):
    session = auth_headers("sam", "viewer")
    _source(database, "sam", "sso")
    r = client.post("/auth/2fa/setup", headers=session, json={"password": ""})
    assert r.status_code == 400 and "identity provider" in r.json()["detail"]
    assert client.post("/auth/webauthn/keys/options", headers=session, json={"password": ""}).status_code == 400


def test_an_sso_account_removes_a_factor_enrolled_before_with_its_code(client, auth_headers, database):
    session = auth_headers("sam", "viewer")
    secret = _enrol(client, session)  # enrolled before 1.3.2, or before the account came from the identity provider
    _source(database, "sam", "sso")
    r = client.post("/auth/2fa/disable", headers=session, json={"password": "", "code": _later_code(secret)})
    assert r.status_code == 200, r.text


def test_a_directory_account_removes_its_factor_with_its_directory_password(
    client, auth_headers, database, monkeypatch
):
    from app.core import ldap_auth

    session = auth_headers("dora", "viewer")
    _source(database, "dora", "ldap")
    monkeypatch.setattr(ldap_auth, "authenticate", lambda u, p: {"username": u} if p == "dir-pw" else None)
    secret = _enrol(client, session, password="dir-pw")
    code = _later_code(secret)
    assert (
        client.post("/auth/2fa/disable", headers=session, json={"password": PASSWORD, "code": code}).status_code == 400
    )
    # Its local hash is random: checked against it, the right password was refused for ever.
    r = client.post("/auth/2fa/disable", headers=session, json={"password": "dir-pw", "code": code})
    assert r.status_code == 200, r.text


def test_an_administrator_resets_an_accounts_second_factors(client, auth_headers, database):
    owner = auth_headers("olga", "viewer")
    _enrol(client, owner)
    admin = auth_headers("root", "admin")
    other = auth_headers("eve", "viewer")
    assert client.post("/auth/users/olga/second-factor/reset", headers=other).status_code == 403
    assert client.post("/auth/users/olga/second-factor/reset", headers=admin).status_code == 200
    assert "require_2fa" not in client.post("/auth/login", data={"username": "olga", "password": PASSWORD}).json()
    assert client.post("/auth/users/nobody/second-factor/reset", headers=admin).status_code == 404
