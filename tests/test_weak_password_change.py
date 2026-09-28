"""A password set before the policy existed is caught at sign-in (the only moment it is known in plain): the
account can then only read who it is and change the password, from the dashboard, nothing else."""

import pyotp

WEAK = "Password2024!"  # a real pre-policy password: too common
GOOD = "Tangerine-Orbit-42"


def _login(client, username, password):
    return client.post("/auth/login", data={"username": username, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _flag(database, username):
    with database.get_conn() as conn:
        return conn.execute("SELECT must_change_password FROM users WHERE username = ?", (username,)).fetchone()[0]


def test_a_strong_password_signs_in_normally(client, make_user, database):
    make_user("amy", "admin", GOOD)
    response = _login(client, "amy", GOOD)
    assert response.json()["password_change_required"] is False
    assert _flag(database, "amy") == 0
    assert client.get("/auth/users", headers=_bearer(response.json()["access_token"])).status_code == 200


def test_a_weak_password_only_allows_changing_it(client, make_user, database):
    make_user("ben", "admin", WEAK)
    response = _login(client, "ben", WEAK)
    assert response.status_code == 200 and response.json()["password_change_required"] is True
    assert _flag(database, "ben") == 1
    session = _bearer(response.json()["access_token"])
    me = client.get("/auth/me", headers=session)
    assert me.status_code == 200 and me.json()["password_change_required"] is True
    refused = client.get("/auth/users", headers=session)
    assert refused.status_code == 403 and refused.json()["detail"] == "Password change required"
    assert client.post("/auth/tokens", headers=session, json={"name": "ci"}).status_code == 403

    changed = client.post("/auth/me/password", headers=session, json={"current_password": WEAK, "new_password": GOOD})
    assert changed.status_code == 200
    assert _flag(database, "ben") == 0
    fresh = _bearer(changed.json()["access_token"])
    assert client.get("/auth/users", headers=fresh).status_code == 200
    assert client.get("/auth/me", headers=fresh).json()["password_change_required"] is False


def test_sessions_opened_before_the_weak_sign_in_are_held_too(client, make_user, database):
    make_user("cat", "admin", WEAK)
    with database.get_conn() as conn:  # an older session, from before the policy
        conn.execute("UPDATE users SET must_change_password = 0 WHERE username = 'cat'")
        conn.commit()
    from app.core.security import create_access_token

    older = _bearer(create_access_token({"sub": "cat", "role": "admin"}))
    assert client.get("/auth/users", headers=older).status_code == 200
    _login(client, "cat", WEAK)
    assert client.get("/auth/users", headers=older).status_code == 403


def test_the_2fa_step_reports_it_too(client, make_user, database):
    make_user("dan", "observateur", WEAK)
    secret = pyotp.random_base32()
    with database.get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = ?, totp_enabled = 1 WHERE username = 'dan'", (secret,))
        conn.commit()
    pre = _login(client, "dan", WEAK).json()["pre_auth_token"]
    done = client.post("/auth/login/2fa", json={"pre_auth_token": pre, "code": pyotp.TOTP(secret).now()})
    assert done.status_code == 200 and done.json()["password_change_required"] is True


def test_the_flag_is_logged_once(client, make_user, database):
    make_user("eva", "observateur", WEAK)
    _login(client, "eva", WEAK)
    _login(client, "eva", WEAK)
    from app.core import audit

    audit._AUDIT_QUEUE.join()
    with database.get_conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE username = 'eva' AND error_message LIKE 'Weak password%'"
        ).fetchone()[0]
    assert rows == 1
