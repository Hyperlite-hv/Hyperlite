"""Authentication and authorization through the real HTTP application."""

import pyotp
import pytest

PASSWORD = "correct horse battery"


def login(client, username, password=PASSWORD):
    return client.post("/auth/login", data={"username": username, "password": password})


def test_login_returns_a_bearer_token(client, make_user):
    make_user("alice", "admin")
    response = login(client, "alice")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer" and body["role"] == "admin" and body["access_token"]


def test_wrong_password_and_unknown_user_get_the_same_answer(client, make_user):
    make_user("alice")
    wrong = login(client, "alice", "nope")
    unknown = login(client, "ghost", "nope")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # no user enumeration


def test_account_is_locked_after_repeated_failures_even_with_the_right_password(client, make_user):
    make_user("alice")
    for _ in range(5):
        assert login(client, "alice", "wrong").status_code == 401
    assert login(client, "alice").status_code == 429


def test_ip_lockout_cannot_be_bypassed_with_a_forged_forwarded_header(client):
    for i in range(20):
        client.post(
            "/auth/login", data={"username": f"user{i}", "password": "x"}, headers={"X-Forwarded-For": f"10.0.0.{i}"}
        )
    blocked = client.post(
        "/auth/login", data={"username": "another", "password": "x"}, headers={"X-Forwarded-For": "203.0.113.50"}
    )
    assert blocked.status_code == 429


@pytest.mark.parametrize(
    "path",
    [
        "/auth/me",
        "/host/profile",
        "/host/limits",
        "/host/capabilities",
        "/nodes",
        "/audit",
        "/notifications/channels",
        "/auth/tokens",
    ],
)
def test_endpoints_require_authentication(client, path):
    assert client.get(path).status_code == 401


def test_invalid_bearer_token_is_rejected(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert response.status_code == 401


ADMIN_ONLY = [
    ("PUT", "/host/profile", {"profil": "standard"}),
    ("PUT", "/host/allocation", {"politique": "libre"}),
    ("GET", "/host/preflight", None),
    ("GET", "/audit", None),
    ("POST", "/auth/users", {"username": "eve", "password": "long enough password", "role": "admin"}),
    ("GET", "/nodes/cluster-pubkey", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ONLY)
def test_observer_cannot_use_administrator_endpoints(client, auth_headers, method, path, body):
    headers = auth_headers("watcher", "observateur")
    response = client.request(method, path, headers=headers, json=body)
    assert response.status_code == 403, f"{method} {path} -> {response.status_code} {response.text}"


def test_observer_can_read_shared_information(client, auth_headers):
    headers = auth_headers("watcher", "observateur")
    assert client.get("/auth/me", headers=headers).json()["role"] == "observateur"
    assert client.get("/host/profile", headers=headers).status_code == 200


def test_observer_cannot_change_the_allocation_policy_it_can_only_read(client, auth_headers):
    admin = auth_headers("root", "admin")
    observer = auth_headers("watcher", "observateur")
    client.put("/host/allocation", headers=admin, json={"politique": "libre"})
    assert client.get("/host/limits", headers=observer).json()["politique"]["actif"] == "libre"
    assert client.put("/host/allocation", headers=observer, json={"politique": "limites"}).status_code == 403
    assert client.get("/host/limits", headers=observer).json()["politique"]["actif"] == "libre"


def test_administrator_can_change_and_reset_the_allocation_policy(client, auth_headers):
    admin = auth_headers("root", "admin")
    assert client.put("/host/allocation", headers=admin, json={"politique": "surallocation"}).status_code == 200
    assert client.get("/host/limits", headers=admin).json()["politique"]["actif"] == "surallocation"
    assert client.put("/host/allocation", headers=admin, json={"politique": "nonsense"}).status_code == 400


def test_forced_environment_policy_cannot_be_overridden_from_the_api(client, auth_headers, monkeypatch):
    admin = auth_headers("root", "admin")
    monkeypatch.setenv("HYPERLITE_ALLOCATION", "libre")
    assert client.put("/host/allocation", headers=admin, json={"politique": "limites"}).status_code == 409


def test_api_token_authenticates_until_it_is_revoked(client, auth_headers):
    session = auth_headers("alice", "admin")
    created = client.post("/auth/tokens", headers=session, json={"name": "automation"})
    assert created.status_code == 201
    token, token_id = created.json()["token"], created.json()["id"]
    api_headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=api_headers).json()["username"] == "alice"
    assert client.delete(f"/auth/tokens/{token_id}", headers=session).status_code == 200
    assert client.get("/auth/me", headers=api_headers).status_code == 401


def test_blank_api_token_names_are_rejected(client, auth_headers):
    session = auth_headers("alice")
    assert client.post("/auth/tokens", headers=session, json={"name": "   "}).status_code == 422


def test_two_factor_login_flow(client, auth_headers):
    session = auth_headers("alice", "admin")
    secret = client.post("/auth/2fa/setup", headers=session).json()["secret"]
    assert client.post("/auth/2fa/confirm", headers=session, json={"code": "abc"}).status_code == 401
    assert client.post("/auth/2fa/confirm", headers=session, json={"code": pyotp.TOTP(secret).now()}).status_code == 200

    first_step = login(client, "alice")
    assert first_step.status_code == 200
    challenge = first_step.json()
    assert challenge["require_2fa"] is True and "access_token" not in challenge

    pre_auth = {"Authorization": f"Bearer {challenge['pre_auth_token']}"}
    assert client.get("/auth/me", headers=pre_auth).status_code == 401  # never usable as a session

    wrong = client.post("/auth/login/2fa", json={"pre_auth_token": challenge["pre_auth_token"], "code": "000001"})
    assert wrong.status_code == 401
    right = client.post(
        "/auth/login/2fa", json={"pre_auth_token": challenge["pre_auth_token"], "code": pyotp.TOTP(secret).now()}
    )
    assert right.status_code == 200 and right.json()["access_token"]


def test_two_factor_can_only_be_disabled_with_the_password(client, auth_headers):
    session = auth_headers("alice")
    secret = client.post("/auth/2fa/setup", headers=session).json()["secret"]
    client.post("/auth/2fa/confirm", headers=session, json={"code": pyotp.TOTP(secret).now()})
    assert client.post("/auth/2fa/disable", headers=session, json={"password": "wrong"}).status_code == 401
    assert client.post("/auth/2fa/disable", headers=session, json={"password": PASSWORD}).status_code == 200
    assert "require_2fa" not in login(client, "alice").json()


def test_two_factor_setup_is_refused_when_already_active(client, auth_headers):
    session = auth_headers("alice")
    secret = client.post("/auth/2fa/setup", headers=session).json()["secret"]
    client.post("/auth/2fa/confirm", headers=session, json={"code": pyotp.TOTP(secret).now()})
    assert client.post("/auth/2fa/setup", headers=session).status_code == 400


def test_notification_channel_configuration_is_only_visible_to_administrators(client, auth_headers):
    admin = auth_headers("root", "admin")
    observer = auth_headers("watcher", "observateur")
    hook = "https://hooks.example.test/services/SECRET-TOKEN"
    created = client.post(
        "/notifications/channels",
        headers=admin,
        json={"type": "webhook", "name": "chat", "config": {"url": hook}, "events": []},
    )
    assert created.status_code == 201

    seen_by_admin = client.get("/notifications/channels", headers=admin).json()
    assert seen_by_admin[0]["config"]["url"] == hook

    seen_by_observer = client.get("/notifications/channels", headers=observer)
    assert seen_by_observer.status_code == 200
    assert seen_by_observer.json()[0]["name"] == "chat"
    assert "SECRET-TOKEN" not in seen_by_observer.text


def test_smtp_password_is_never_returned_even_to_administrators(client, auth_headers):
    admin = auth_headers("root", "admin")
    client.post(
        "/notifications/channels",
        headers=admin,
        json={
            "type": "email",
            "name": "mail",
            "events": [],
            "config": {"smtp_host": "smtp.example.test", "smtp_password": "hunter2", "to": "ops@example.test"},
        },
    )
    response = client.get("/notifications/channels", headers=admin)
    assert "hunter2" not in response.text
    assert response.json()[0]["config"]["smtp_password_set"] is True
