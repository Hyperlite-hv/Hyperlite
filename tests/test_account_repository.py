"""The account repository (control plane v2, lot 6): users, TOTP state, API tokens, signed-out sessions and the
brute-force counters, run against every backend (SQLite today)."""

import asyncio

import pytest

from app.domain.common import AlreadyExists


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.accounts import SqliteAccountRepository

    return SqliteAccountRepository()


def run(coro):
    return asyncio.run(coro)


def test_users(repo):
    store = repo.sync
    store.create("alice", "hash-a", "admin")
    store.create("bob", "hash-b", "observateur", auth_source="ldap", ldap_dn="uid=bob")
    with pytest.raises(AlreadyExists):
        store.create("alice", "x", "admin")
    assert [u["username"] for u in run(repo.list_summary())] == ["alice", "bob"]
    assert store.other_admins("alice") == 0 and store.role_of("bob") == "observateur"
    store.update("bob", role="admin", hashed_password="hash-b2", changed_at=123)
    bob = run(repo.get("bob"))
    assert (bob["role"], bob["hashed_password"], bob["password_changed_at"], bob["must_change_password"]) == (
        "admin",
        "hash-b2",
        123,
        0,
    )
    store.flag_must_change("bob")
    store.record_login("bob", "t")
    assert store.get("bob")["must_change_password"] == 1 and store.get("bob")["last_login_at"] == "t"
    store.delete("bob")
    assert store.get("bob") is None


def test_totp_codes_are_used_once(repo):
    store = repo.sync
    store.create("alice", "h", "admin")
    store.set_totp_secret("alice", "sealed")
    store.enable_totp("alice")
    assert store.claim_totp_step("alice", 100) and not store.claim_totp_step("alice", 100)
    assert store.claim_totp_step("alice", 101)
    assert store.totp_secrets() == [{"username": "alice", "totp_secret": "sealed"}]
    store.disable_totp("alice")
    assert (store.get("alice")["totp_enabled"], store.get("alice")["totp_secret"]) == (0, None)


def test_tokens_sessions_and_failures(repo):
    store = repo.sync
    store.create("alice", "h", "admin")
    token_id = store.create_token("alice", "ci", "digest", "t0", None, "api")
    assert store.token_by_hash("digest")["id"] == token_id and store.token_by_hash("other") is None
    store.touch_token("digest", "t1")
    assert run(repo.list_tokens("alice"))[0]["last_used_at"] == "t1"
    assert not store.revoke_token("bob", token_id) and store.revoke_token("alice", token_id)
    store.create_token("alice", "a", "d1", "t", None, "api")
    store.create_token("alice", "b", "d2", "t", None, "cli")
    assert store.revoke_all_tokens("alice") == 2

    store.revoke_session("jti-1", 200, 100)
    assert store.session_revoked("jti-1") and not store.session_revoked("jti-2")
    store.revoke_session("jti-2", 400, 300)  # expired entries are dropped on the way
    assert not store.session_revoked("jti-1")

    store.record_failure("user", "alice", 1000, 0)
    store.record_failure("user", "alice", 1010, 0)
    assert store.failures_since("user", "alice", 1005) == 1
    store.clear_failures("user", "alice")
    assert store.failures_since("user", "alice", 0) == 0
