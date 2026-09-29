"""Brute-force lock: it lives in the database (a restart does not reset it), knowing the password never
buys unlimited guesses of the 2FA code, and removing 2FA takes the password, a current code and a session."""

import time

import pyotp
import pytest

from app.core import login_guard
from app.core.twofa import seal_secret

PASSWORD = "correct horse battery"  # the fixtures' default password


def _login(client, username, password=PASSWORD):
    return client.post("/auth/login", data={"username": username, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def totp_user(make_user, database):
    """A user with 2FA on; returns (username, secret)."""

    def _make(username):
        make_user(username, "observateur", PASSWORD)
        secret = pyotp.random_base32()
        with database.get_conn() as conn:
            conn.execute(
                "UPDATE users SET totp_secret = ?, totp_enabled = 1 WHERE username = ?", (seal_secret(secret), username)
            )
            conn.commit()
        return username, secret

    return _make


def _next_code(secret):
    """The code of the next 30 s step: a code is accepted once, so a second action right after signing in with the
    current code needs the next one (still inside the +-1 step window)."""
    return pyotp.TOTP(secret).at(time.time() + 30)


def _wrong_code(secret):
    right = pyotp.TOTP(secret)
    return next(c for c in ("000000", "111111", "222222") if not right.verify(c, valid_window=1))


def test_the_lock_is_read_from_the_database(client, make_user, database):
    make_user("ada")
    now = time.time()
    with database.get_conn() as conn:  # failures written by an earlier run of the service
        conn.executemany(
            "INSERT INTO login_failures (kind, key, at) VALUES ('userip', 'ada|testclient', ?)",
            [(now - i,) for i in range(5)],
        )
        conn.commit()
    assert _login(client, "ada").status_code == 429


def test_failures_outside_the_window_do_not_count(client, make_user, database):
    make_user("bob")
    old = time.time() - 400
    with database.get_conn() as conn:
        conn.executemany(
            "INSERT INTO login_failures (kind, key, at) VALUES ('userip', 'bob|testclient', ?)",
            [(old - i,) for i in range(5)],
        )
        conn.commit()
    assert _login(client, "bob").status_code == 200


def test_wrong_passwords_lock_the_account_and_a_success_clears_it(client, make_user, database):
    make_user("cleo")
    for _ in range(4):
        assert _login(client, "cleo", "nope nope nope").status_code == 401
    assert _login(client, "cleo").status_code == 200
    assert login_guard.recent_failures("userip", "cleo|testclient", 300) == 0
    for _ in range(5):
        assert _login(client, "cleo", "nope nope nope").status_code == 401
    assert _login(client, "cleo").status_code == 429


def test_old_rows_are_pruned(database):
    with database.get_conn() as conn:
        conn.execute("INSERT INTO login_failures (kind, key, at) VALUES ('ip', '10.0.0.1', ?)", (time.time() - 7200,))
        conn.commit()
    login_guard.record_failure("ip", "10.0.0.2")
    with database.get_conn() as conn:
        keys = [r[0] for r in conn.execute("SELECT key FROM login_failures")]
    assert keys == ["10.0.0.2"]


def test_knowing_the_password_does_not_reset_the_2fa_lock(client, totp_user):
    username, secret = totp_user("dora")
    bad = _wrong_code(secret)
    for _ in range(5):  # sign in again with the right password before every wrong code
        pre = _login(client, username).json()["pre_auth_token"]
        assert client.post("/auth/login/2fa", json={"pre_auth_token": pre, "code": bad}).status_code == 401
    assert _login(client, username).status_code == 429


def test_removing_2fa_needs_the_password_and_a_current_code(client, totp_user, database):
    username, secret = totp_user("eve")
    pre = _login(client, username).json()["pre_auth_token"]
    token = client.post("/auth/login/2fa", json={"pre_auth_token": pre, "code": pyotp.TOTP(secret).now()}).json()
    session = _bearer(token["access_token"])
    no_code = client.post("/auth/2fa/disable", headers=session, json={"password": PASSWORD})
    assert no_code.status_code == 400 and "2FA" in no_code.json()["detail"]
    wrong_pw = client.post(
        "/auth/2fa/disable", headers=session, json={"password": "nope nope", "code": _next_code(secret)}
    )
    assert wrong_pw.status_code == 400
    ok = client.post("/auth/2fa/disable", headers=session, json={"password": PASSWORD, "code": _next_code(secret)})
    assert ok.status_code == 200
    with database.get_conn() as conn:
        row = conn.execute("SELECT totp_enabled, totp_secret FROM users WHERE username = 'eve'").fetchone()
    assert row["totp_enabled"] == 0 and row["totp_secret"] is None


def test_removing_2fa_locks_out_after_repeated_failures(client, totp_user, database):
    username, secret = totp_user("finn")
    pre = _login(client, username).json()["pre_auth_token"]
    token = client.post("/auth/login/2fa", json={"pre_auth_token": pre, "code": pyotp.TOTP(secret).now()}).json()
    session = _bearer(token["access_token"])
    bad = _wrong_code(secret)
    for _ in range(5):
        wrong = client.post("/auth/2fa/disable", headers=session, json={"password": PASSWORD, "code": bad})
        assert wrong.status_code == 400  # not 401: the dashboard must not sign out
    locked = client.post("/auth/2fa/disable", headers=session, json={"password": PASSWORD, "code": _next_code(secret)})
    assert locked.status_code == 429
    with database.get_conn() as conn:
        assert conn.execute("SELECT totp_enabled FROM users WHERE username = 'finn'").fetchone()[0] == 1


def test_an_api_token_cannot_manage_2fa(client, make_user):
    make_user("gus", "admin", PASSWORD)
    session = _bearer(_login(client, "gus").json()["access_token"])
    api = _bearer(client.post("/auth/tokens", headers=session, json={"name": "ci"}).json()["token"])
    assert client.post("/auth/2fa/setup", headers=api, json={"password": PASSWORD}).status_code == 403
    assert client.post("/auth/2fa/confirm", headers=api, json={"code": "123456"}).status_code == 403
    assert client.post("/auth/2fa/disable", headers=api, json={"password": PASSWORD}).status_code == 403


# --- The lock cannot be turned against the owner ---


def test_failures_from_another_address_do_not_lock_the_owner_out(client, make_user, database):
    make_user("hana")
    now = time.time()
    with database.get_conn() as conn:  # an attacker elsewhere, hammering the account
        conn.executemany(
            "INSERT INTO login_failures (kind, key, at) VALUES ('userip', 'hana|203.0.113.7', ?)",
            [(now - i,) for i in range(10)],
        )
        conn.executemany(
            "INSERT INTO login_failures (kind, key, at) VALUES ('user', 'hana', ?)", [(now - i,) for i in range(10)]
        )
        conn.commit()
    assert _login(client, "hana").status_code == 200


def test_a_guessing_attack_spread_over_many_addresses_still_locks_the_account(client, make_user, database):
    make_user("ivo")
    now = time.time()
    with database.get_conn() as conn:
        conn.executemany(
            "INSERT INTO login_failures (kind, key, at) VALUES ('user', 'ivo', ?)", [(now - i,) for i in range(50)]
        )
        conn.commit()
    assert _login(client, "ivo").status_code == 429


def test_failed_sign_ins_do_not_stop_the_owner_from_securing_the_account(client, totp_user, database):
    username, secret = totp_user("jade")
    pre = _login(client, username).json()["pre_auth_token"]
    session = _bearer(
        client.post("/auth/login/2fa", json={"pre_auth_token": pre, "code": pyotp.TOTP(secret).now()}).json()[
            "access_token"
        ]
    )
    now = time.time()
    with database.get_conn() as conn:
        conn.executemany(
            "INSERT INTO login_failures (kind, key, at) VALUES ('userip', 'jade|203.0.113.7', ?)",
            [(now - i,) for i in range(10)],
        )
        conn.commit()
    ok = client.post("/auth/2fa/disable", headers=session, json={"password": PASSWORD, "code": _next_code(secret)})
    assert ok.status_code == 200


def test_a_totp_code_is_accepted_once(client, totp_user):
    username, secret = totp_user("kai")
    code = pyotp.TOTP(secret).now()
    first = client.post(
        "/auth/login/2fa", json={"pre_auth_token": _login(client, username).json()["pre_auth_token"], "code": code}
    )
    assert first.status_code == 200
    replay = client.post(
        "/auth/login/2fa", json={"pre_auth_token": _login(client, username).json()["pre_auth_token"], "code": code}
    )
    assert replay.status_code == 401
