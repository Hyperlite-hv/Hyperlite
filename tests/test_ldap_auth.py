"""Signing in with a directory account, against ldap3's in-memory mock directory (bind and search for real)."""

import pytest
from ldap3 import MOCK_SYNC, OFFLINE_SLAPD_2_4, Connection, Server

from app.core import ldap_auth

BASE = "dc=example,dc=org"
ADMINS = f"cn=hv-admins,ou=groups,{BASE}"
STAFF = f"cn=staff,ou=groups,{BASE}"


@pytest.fixture()
def directory(database, monkeypatch):
    server = Server("mock", get_info=OFFLINE_SLAPD_2_4)
    seed = Connection(server, user="cn=svc,dc=example,dc=org", password="svcpw", client_strategy=MOCK_SYNC)
    seed.strategy.add_entry("cn=svc,dc=example,dc=org", {"objectClass": ["person"], "userPassword": "svcpw"})
    seed.strategy.add_entry(
        f"uid=alice,ou=people,{BASE}",
        {"objectClass": ["person"], "uid": "alice", "userPassword": "alicepw", "memberOf": [ADMINS, STAFF]},
    )
    seed.strategy.add_entry(
        f"uid=bob,ou=people,{BASE}",
        {"objectClass": ["person"], "uid": "bob", "userPassword": "bobpw", "memberOf": [STAFF]},
    )
    seed.strategy.add_entry(
        f"uid=mallory,ou=people,{BASE}",
        {
            "objectClass": ["person"],
            "uid": "mallory",
            "userPassword": "mallorypw",
            "memberOf": [f"cn=hv-admins,ou=lab,{BASE}", STAFF],  # a group of the same name, made in another OU
        },
    )
    seed.strategy.add_entry(
        f"uid=eve,ou=people,{BASE}", {"objectClass": ["person"], "uid": "eve", "userPassword": "evepw"}
    )
    seed.strategy.add_entry(
        f"uid=root,ou=people,{BASE}", {"objectClass": ["person"], "uid": "root", "userPassword": "dirpw"}
    )
    monkeypatch.setattr(ldap_auth, "_server", lambda cfg: server)

    def connect(cfg, srv, user, password):
        conn = Connection(srv, user=user, password=password, client_strategy=MOCK_SYNC, raise_exceptions=False)
        conn.open()
        return conn

    monkeypatch.setattr(ldap_auth, "_connect", connect)
    ldap_auth.set_config(
        {
            "enabled": True,
            "url": "ldap://dir.example.org",
            "bind_dn": "cn=svc,dc=example,dc=org",
            "bind_password": "svcpw",
            "base_dn": BASE,
            "user_filter": "(&(objectClass=person)(uid={username}))",
            "group_attribute": "memberOf",
            "admin_groups": ADMINS,
            "allowed_groups": STAFF,
        }
    )
    return server


def test_directory_users_sign_in_with_their_role(client, directory):
    r = client.post("/auth/login", data={"username": "alice", "password": "alicepw"})
    assert r.status_code == 200, r.text
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert (me["role"], me["auth_source"]) == ("admin", "ldap")
    r = client.post("/auth/login", data={"username": "bob", "password": "bobpw"})
    assert r.status_code == 200
    from app.core.security import get_user

    assert get_user("bob")["role"] == "viewer" and get_user("bob")["ldap_dn"] == f"uid=bob,ou=people,{BASE}"


@pytest.mark.parametrize(
    ("username", "password"),
    [("bob", "wrong"), ("bob", ""), ("eve", "evepw"), ("nobody", "x"), ("*", "x"), ("bob)(uid=*", "bobpw")],
)
def test_refusals(client, directory, username, password):
    # a wrong or empty password (an anonymous bind), a user in no allowed group, an unknown user, filter tricks
    assert client.post("/auth/login", data={"username": username, "password": password}).status_code in (401, 422)


def test_a_local_account_is_never_taken_over(client, directory, auth_headers):
    auth_headers("root", "admin")  # a local account named like a directory user
    assert client.post("/auth/login", data={"username": "root", "password": "dirpw"}).status_code == 401


def test_role_follows_the_directory_at_each_sign_in(client, directory):
    assert client.post("/auth/login", data={"username": "alice", "password": "alicepw"}).status_code == 200
    ldap_auth.set_config({**ldap_auth.public_config(), "admin_groups": f"cn=someone-else,{BASE}"})
    assert client.post("/auth/login", data={"username": "alice", "password": "alicepw"}).status_code == 200
    from app.core.security import get_user

    assert get_user("alice")["role"] == "viewer"
    ldap_auth.set_config({**ldap_auth.public_config(), "enabled": False})
    assert client.post("/auth/login", data={"username": "alice", "password": "alicepw"}).status_code == 401


def test_directory_accounts_do_not_get_a_local_password(client, directory):
    token = client.post("/auth/login", data={"username": "bob", "password": "bobpw"}).json()["access_token"]
    r = client.post(
        "/auth/me/password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "bobpw", "new_password": "An0ther-Long-Passw0rd!"},
    )
    assert r.status_code == 400 and "directory" in r.json()["detail"]


def test_settings_api(client, auth_headers, directory):
    admin = auth_headers("root", "admin")
    cfg = client.get("/ldap/config", headers=admin).json()
    assert cfg["bind_password_set"] is True and "bind_password" not in cfg
    r = client.post("/ldap/test", json={"username": "alice", "password": "alicepw"}, headers=admin)
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    assert client.post("/ldap/test", json={"username": "alice", "password": "no"}, headers=admin).json()["user"] == {
        "accepted": False
    }
    for bad, message in [
        ({"url": "http://x", "base_dn": BASE}, "ldap://"),
        ({"url": "ldaps://x", "starttls": True, "base_dn": BASE}, "StartTLS"),
        ({"url": "ldap://x", "base_dn": "not a dn"}, "Invalid DN"),
        ({"url": "ldap://x", "base_dn": BASE, "user_filter": "(uid=x)"}, "{username}"),
        ({"url": "ldap://x", "base_dn": BASE, "ca_cert": "nope"}, "PEM"),
    ]:
        r = client.put("/ldap/config", json=bad, headers=admin)
        assert r.status_code == 422 and message in r.json()["detail"], bad
    assert client.get("/ldap/config", headers=auth_headers("watcher", "viewer")).status_code == 403


# --- GHSA-8qgj-pvwg-xj37 ----------------------------------------------------------------------------------------------


def test_a_group_of_the_same_name_elsewhere_in_the_directory_is_not_the_admin_group(client, directory):
    assert client.post("/auth/login", data={"username": "mallory", "password": "mallorypw"}).status_code == 200
    from app.core.security import get_user

    assert get_user("mallory")["role"] == "viewer"


def test_groups_are_saved_as_full_dns_only(client, auth_headers, directory):
    admin = auth_headers("root", "admin")
    r = client.put("/ldap/config", json={**ldap_auth.public_config(), "admin_groups": "hv-admins"}, headers=admin)
    assert r.status_code == 422 and "full DN" in r.json()["detail"]


def test_bare_names_saved_before_match_nothing_and_still_restrict(client, directory):
    ldap_auth._store().save_ldap_config({"admin_groups": "hv-admins", "allowed_groups": "staff"})
    assert client.post("/auth/login", data={"username": "alice", "password": "alicepw"}).status_code == 401
    assert client.post("/auth/login", data={"username": "bob", "password": "bobpw"}).status_code == 401


def test_a_directory_name_is_one_account_and_one_lock_whatever_its_case(client, directory):
    assert client.post("/auth/login", data={"username": "Bob", "password": "bobpw"}).status_code == 200
    from app.core.security import get_user

    assert get_user("bob") is not None and get_user("Bob") is None
    from app.routers import auth

    for name in ("bob", "Bob", "BOB", "bOb", "boB"):
        client.post("/auth/login", data={"username": name, "password": "wrong"})
    assert auth._login_locked_out("BoB", "testclient")
