"""Password policy, administrator reset and self-service change: what they refuse, and that an old session,
an API token or a wrong current password never gets through."""

import time

import pytest

from app.core.password_policy import password_problem

GOOD = "Tangerine-Orbit-42"
OLD = "correct horse battery"  # the fixtures' default password


@pytest.mark.parametrize(
    ("password", "why"),
    [
        ("short-one", "at least 12"),
        ("é" * 37, "72 bytes"),
        ("my-alice-account-pw", "account name"),
        ("abababababab", "repetitive"),
        ("123456789012", "sequence"),
        ("azertyuiopqs", "sequence"),
        ("P@ssw0rd2024!", "too common"),
        ("Azerty123456!", "too common"),
        ("Hyperlite2026", "too common"),
    ],
)
def test_policy_refuses_weak_passwords(password, why):
    problem = password_problem(password, "alice")
    assert problem is not None and why in problem


@pytest.mark.parametrize("password", [GOOD, "correct horse battery staple", "Vélo-du-matin-7"])
def test_policy_accepts_long_uncommon_passwords(password):
    assert password_problem(password, "alice") is None


def _login(client, username, password):
    return client.post("/auth/login", data={"username": username, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- creation


def test_create_user_applies_the_policy(client, auth_headers):
    admin = auth_headers("root-admin")
    weak = client.post(
        "/auth/users", headers=admin, json={"username": "bob", "password": "pppp", "role": "observateur"}
    )
    assert weak.status_code == 422 and "at least 12" in weak.json()["detail"]
    ok = client.post("/auth/users", headers=admin, json={"username": "bob", "password": GOOD, "role": "observateur"})
    assert ok.status_code == 201


# ---------------------------------------------------------------- administrator reset


def test_admin_reset_signs_out_the_user_and_revokes_its_tokens(client, auth_headers, make_user):
    admin = auth_headers("root-admin")
    make_user("carol", "observateur", OLD)
    carol = _bearer(_login(client, "carol", OLD).json()["access_token"])
    api = client.post("/auth/tokens", headers=carol, json={"name": "script"}).json()["token"]
    assert client.get("/auth/me", headers=_bearer(api)).status_code == 200

    time.sleep(1.1)  # iat has a one-second resolution: the old token must predate the change
    reset = client.patch("/auth/users/carol", headers=admin, json={"password": GOOD})
    assert reset.status_code == 200 and reset.json()["revoked_tokens"] == 1

    assert client.get("/auth/me", headers=carol).status_code == 401
    assert client.get("/auth/me", headers=_bearer(api)).status_code == 401
    assert _login(client, "carol", OLD).status_code == 401
    assert _login(client, "carol", GOOD).status_code == 200


def test_admin_reset_refuses_weak_passwords_sso_accounts_and_self(client, auth_headers, make_user, database):
    admin = auth_headers("root-admin")
    make_user("dave", "observateur", OLD)
    weak = client.patch("/auth/users/dave", headers=admin, json={"password": "dave-password"})
    assert weak.status_code == 422
    assert _login(client, "dave", OLD).status_code == 200  # nothing was written

    with database.get_conn() as conn:
        conn.execute("UPDATE users SET auth_source = 'sso' WHERE username = 'dave'")
        conn.commit()
    assert client.patch("/auth/users/dave", headers=admin, json={"password": GOOD}).status_code == 400

    own = client.patch("/auth/users/root-admin", headers=admin, json={"password": GOOD})
    assert own.status_code == 400 and "Change my password" in own.json()["detail"]


def test_observer_cannot_reset_anyone(client, auth_headers, make_user):
    observer = auth_headers("olivia", role="observateur")
    make_user("erin", "admin", OLD)
    assert client.patch("/auth/users/erin", headers=observer, json={"password": GOOD}).status_code == 403


# ---------------------------------------------------------------- self-service change


def test_change_my_password_signs_out_other_sessions_and_keeps_this_one(client, make_user):
    make_user("frank", "observateur", OLD)
    first = _bearer(_login(client, "frank", OLD).json()["access_token"])
    other = _bearer(_login(client, "frank", OLD).json()["access_token"])
    time.sleep(1.1)
    changed = client.post("/auth/me/password", headers=first, json={"current_password": OLD, "new_password": GOOD})
    assert changed.status_code == 200
    fresh = _bearer(changed.json()["access_token"])
    assert client.get("/auth/me", headers=fresh).status_code == 200
    assert client.get("/auth/me", headers=first).status_code == 401
    assert client.get("/auth/me", headers=other).status_code == 401
    assert _login(client, "frank", GOOD).status_code == 200


@pytest.mark.parametrize(("remember", "days"), [(True, 7), (False, 0)])
def test_change_my_password_keeps_stay_signed_in(client, make_user, remember, days):
    import jwt

    make_user(f"kim{days}", "observateur", OLD)
    data = {"username": f"kim{days}", "password": OLD}
    if remember:
        data["remember"] = "true"
    session = _bearer(client.post("/auth/login", data=data).json()["access_token"])
    changed = client.post("/auth/me/password", headers=session, json={"current_password": OLD, "new_password": GOOD})
    claims = jwt.decode(changed.json()["access_token"], options={"verify_signature": False})
    assert (claims["exp"] - claims["iat"]) // 86400 == days


def test_change_my_password_checks_the_current_one_and_locks_out(client, make_user):
    make_user("gina", "observateur", OLD)
    session = _bearer(_login(client, "gina", OLD).json()["access_token"])
    for _ in range(5):
        wrong = client.post(
            "/auth/me/password", headers=session, json={"current_password": "not it at all", "new_password": GOOD}
        )
        assert wrong.status_code == 400  # not 401: the dashboard must not sign out
    locked = client.post("/auth/me/password", headers=session, json={"current_password": OLD, "new_password": GOOD})
    assert locked.status_code == 429


def test_change_my_password_refuses_reuse_and_weak_passwords(client, make_user):
    make_user("hugo", "observateur", OLD)
    session = _bearer(_login(client, "hugo", OLD).json()["access_token"])
    same = client.post("/auth/me/password", headers=session, json={"current_password": OLD, "new_password": OLD})
    assert same.status_code == 422
    weak = client.post(
        "/auth/me/password", headers=session, json={"current_password": OLD, "new_password": "Password2024!"}
    )
    assert weak.status_code == 422 and "too common" in weak.json()["detail"]


def test_change_my_password_needs_the_2fa_code_when_enabled(client, make_user, database):
    import pyotp

    make_user("iris", "observateur", OLD)
    secret = pyotp.random_base32()
    with database.get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = ?, totp_enabled = 1 WHERE username = 'iris'", (secret,))
        conn.commit()
    pre = _login(client, "iris", OLD).json()["pre_auth_token"]
    token = client.post("/auth/login/2fa", json={"pre_auth_token": pre, "code": pyotp.TOTP(secret).now()}).json()[
        "access_token"
    ]
    session = _bearer(token)
    missing = client.post("/auth/me/password", headers=session, json={"current_password": OLD, "new_password": GOOD})
    assert missing.status_code == 400 and "2FA" in missing.json()["detail"]
    ok = client.post(
        "/auth/me/password",
        headers=session,
        json={"current_password": OLD, "new_password": GOOD, "code": pyotp.TOTP(secret).at(time.time() + 30)},
    )
    assert ok.status_code == 200


def test_api_token_cannot_change_the_account_password(client, make_user):
    make_user("jack", "admin", OLD)
    session = _bearer(_login(client, "jack", OLD).json()["access_token"])
    api = client.post("/auth/tokens", headers=session, json={"name": "ci"}).json()["token"]
    refused = client.post(
        "/auth/me/password", headers=_bearer(api), json={"current_password": OLD, "new_password": GOOD}
    )
    assert refused.status_code == 403
    assert _login(client, "jack", OLD).status_code == 200


def test_the_audit_log_never_holds_a_password(client, auth_headers, make_user, database):
    from app.core import audit

    admin = auth_headers("root-admin")
    make_user("kate", "observateur", OLD)
    client.patch("/auth/users/kate", headers=admin, json={"password": GOOD})
    audit._AUDIT_QUEUE.join()
    with database.get_conn() as conn:
        rows = [" ".join(str(v) for v in dict(r).values()) for r in conn.execute("SELECT * FROM audit_log")]
    assert any("reset_password" in r for r in rows)
    assert not any(GOOD in r or OLD in r for r in rows)
