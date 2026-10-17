"""A deleted account leaves nothing that signs in (#448): not its API tokens, not its sessions, even when an account is
later created under the same name (a new colleague also called Bob). Its tokens used to stay in the database and were
found by name: they worked again for the new account, with the new account's rights."""

PASSWORD = "correct horse battery"


def _login(client, username, password=PASSWORD):
    r = client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_a_deleted_accounts_tokens_and_sessions_never_sign_in_again(client, auth_headers):
    admin = auth_headers("root")
    assert client.post("/auth/users", json={"username": "bob", "password": PASSWORD}, headers=admin).status_code == 201
    old_session = _login(client, "bob")
    r = client.post("/auth/tokens", json={"name": "script", "expires_days": None}, headers=old_session)
    assert r.status_code in (200, 201), r.text
    old_token = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/auth/me", headers=old_token).status_code == 200

    assert client.delete("/auth/users/bob", headers=admin).status_code == 200
    assert client.get("/auth/me", headers=old_token).status_code == 401
    assert client.get("/auth/me", headers=old_session).status_code == 401

    # A new colleague, also "bob", this time an administrator.
    new = {"username": "bob", "password": "another long passphrase", "role": "admin"}
    assert client.post("/auth/users", json=new, headers=admin).status_code == 201
    assert client.get("/auth/me", headers=old_token).status_code == 401  # the former Bob's script
    assert client.get("/auth/me", headers=old_session).status_code == 401  # the former Bob's browser
    assert client.get("/auth/me", headers=_login(client, "bob", "another long passphrase")).status_code == 200


def test_deleting_an_account_removes_its_tokens(client, auth_headers, database):
    admin = auth_headers("root")
    client.post("/auth/users", json={"username": "carol", "password": PASSWORD}, headers=admin)
    client.post("/auth/tokens", json={"name": "ci"}, headers=_login(client, "carol"))
    client.delete("/auth/users/carol", headers=admin)
    with database.get_conn() as db:
        assert db.execute("SELECT COUNT(*) FROM api_tokens WHERE username = 'carol'").fetchone()[0] == 0
