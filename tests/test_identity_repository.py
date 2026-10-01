"""The identity repository (control plane v2, lot 7): security keys and their challenges, the SSO and LDAP
configurations, the SSO state and handoffs, and the accounts those providers create, run against every backend
(SQLite today)."""

import asyncio

import pytest

from app.repositories.sqlite.identity import LOCAL_ACCOUNT, OTHER_SUBJECT, PROVISIONED


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.identity import SqliteIdentityRepository

    return SqliteIdentityRepository()


def run(coro):
    return asyncio.run(coro)


def test_security_keys(repo, make_user):
    store = repo.sync
    make_user("alice")
    assert not run(repo.has_keys("alice"))
    key = store.add_credential("alice", "YubiKey", "cred-1", "pk", 0, "hv.lan", "2026-01-01")
    assert store.add_credential("bob", "Copy", "cred-1", "pk", 0, "hv.lan", "2026-01-02") is None
    assert run(repo.has_keys("alice")) and store.credential_ids("alice", "hv.lan") == ["cred-1"]
    assert store.credential_ids("alice", "other.lan") == []
    store.record_key_use(key, 7, "2026-01-03")
    assert store.credential("alice", "cred-1", "hv.lan")["sign_count"] == 7
    assert [k["utilise_le"] for k in run(repo.list_keys("alice"))] == ["2026-01-03"]
    assert not store.delete_key("bob", key) and store.delete_key("alice", key)
    store.add_credential("alice", "Second", "cred-2", "pk", 0, "hv.lan", "2026-01-04")
    store.delete_all_keys("alice")
    assert not store.has_keys("alice")


def test_challenges_are_single_use_and_purged(repo):
    store = repo.sync
    store.store_challenge("alice", "login", "old", "hv.lan", "https://hv.lan", "2026-01-01T00:02", "2026-01-01")
    store.store_challenge("alice", "login", "c1", "hv.lan", "https://hv.lan", "2026-01-02T00:02", "2026-01-02")
    assert store.take_challenge("alice", "login", "old", "hv.lan", "https://hv.lan") is None  # purged
    assert store.take_challenge("alice", "register", "c1", "hv.lan", "https://hv.lan") is None  # another purpose
    assert store.take_challenge("alice", "login", "c1", "hv.lan", "https://hv.lan") == "2026-01-02T00:02"
    assert store.take_challenge("alice", "login", "c1", "hv.lan", "https://hv.lan") is None


def test_sso_and_ldap_configurations(repo):
    store = repo.sync
    assert run(repo.sso_config()) is None and run(repo.ldap_config()) is None
    store.save_sso_config({"issuer": "https://idp", "client_id": "hl"})
    store.save_sso_config({"client_id": "hl2"})
    assert (store.sso_config()["issuer"], store.sso_config()["client_id"]) == ("https://idp", "hl2")
    store.save_ldap_config({"url": "ldaps://dc", "base_dn": "dc=lan"})
    store.save_ldap_config({"base_dn": "dc=corp"})
    assert (store.ldap_config()["url"], store.ldap_config()["base_dn"]) == ("ldaps://dc", "dc=corp")


def test_sso_state_and_handoffs_are_single_use(repo):
    store = repo.sync
    store.put_sso_state("s0", "n0", 10.0, "b0", 0.0)
    store.put_sso_state("s1", "n1", 100.0, "b1", 50.0)  # purges s0
    assert store.take_sso_state("s0") is None
    assert store.take_sso_state("s1") == {"nonce": "n1", "created_at": 100.0, "binding": "b1"}
    assert store.take_sso_state("s1") is None
    store.put_handoff("h", "alice", 5.0, 0.0)
    assert store.take_handoff("h") == {"username": "alice", "created_at": 5.0}
    assert store.take_handoff("h") is None


def test_provisioning_never_takes_over_another_account(repo, make_user):
    store = repo.sync
    make_user("local")
    hashed = []

    def new_hash():
        hashed.append(1)
        return "random-hash"

    assert store.provision_sso_user("local", "admin", "sub-1", new_hash)[0] == LOCAL_ACCOUNT
    outcome, row = store.provision_sso_user("carol", "observateur", "sub-c", new_hash)
    assert outcome == PROVISIONED and (row["auth_source"], row["sso_subject"]) == ("sso", "sub-c")
    # Found again by its subject even when the IdP now proposes another name; the role follows the IdP.
    outcome, row = store.provision_sso_user("carol.new", "admin", "sub-c", new_hash)
    assert (outcome, row["username"], row["role"]) == (PROVISIONED, "carol", "admin")
    assert store.provision_sso_user("carol", "admin", "sub-other", new_hash)[0] == OTHER_SUBJECT
    assert len(hashed) == 1  # only the new account got a password hash

    assert store.provision_ldap_user("carol", "uid=carol", "admin", new_hash)[0] == LOCAL_ACCOUNT
    outcome, row = store.provision_ldap_user("dan", "uid=dan", "observateur", new_hash)
    assert outcome == PROVISIONED and (row["auth_source"], row["ldap_dn"]) == ("ldap", "uid=dan")
    outcome, row = store.provision_ldap_user("dan", "uid=dan,ou=x", "admin", new_hash)
    assert (row["role"], row["ldap_dn"]) == ("admin", "uid=dan,ou=x") and len(hashed) == 2
