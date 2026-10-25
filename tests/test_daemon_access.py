"""The permissions written by hyperlite-daemon only (docs/design/privilege-separation.md, section 14.1, step 4b): the
API never writes the accounts, tokens, keys, second factors, groups, roles, pools or ACLs itself."""

import inspect
import re

import pyotp
import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import privileged
from app.daemon.registry import OPERATIONS, DaemonError
from app.repositories.sqlite import access, accounts, identity, renames

TABLES = (
    "users",
    "api_tokens",
    "webauthn_credentials",
    "revoked_sessions",
    "groups",
    "group_members",
    "custom_roles",
    "acl",
    "pools",
    "pool_members",
)
WRITES = re.compile(r"\b(?:INSERT(?: OR \w+)? INTO|UPDATE|DELETE FROM|REPLACE INTO)\s+(\w+)")
STORES = (
    accounts.SqliteAccountStore,
    identity.SqliteIdentityStore,
    access.SqliteAccessStore,
    renames.SqliteRenameStore,
)


def _methods():
    for store in STORES:
        for name, fn in inspect.getmembers(store, inspect.isfunction):
            yield store, name, fn


def test_every_write_of_the_permissions_is_the_daemons():
    """A new method that writes one of these tables without @daemon_write would let the API write it again."""
    marked = set()
    for store, name, fn in _methods():
        body = inspect.getsource(inspect.unwrap(fn))
        written = {t for t in WRITES.findall(body) if t in TABLES}
        if written:
            assert hasattr(fn, "daemon_op"), f"{store.__name__}.{name} writes {sorted(written)} outside the daemon"
        if hasattr(fn, "daemon_op"):
            marked.add(fn.daemon_op)
    assert len(marked) == 42
    assert marked <= set(OPERATIONS), sorted(marked - set(OPERATIONS))
    # A method of the API's own state (the sign-in's challenges, its failure counters) is not the daemon's.
    assert not hasattr(identity.SqliteIdentityStore.store_challenge, "daemon_op")
    assert not hasattr(accounts.SqliteAccountStore.record_failure, "daemon_op")


def test_the_api_never_writes_them_itself(database, make_user, monkeypatch, tmp_path):
    """Without the daemon, a write is refused, and the table is unchanged: the API has no way round it."""
    from app.repositories import registry

    make_user("alice", "viewer")
    monkeypatch.delenv(privileged.INPROCESS_ENV)
    monkeypatch.setenv("HYPERLITE_DAEMON_SOCKET", str(tmp_path / "none.sock"))
    store = registry.accounts().sync
    with pytest.raises(DaemonError) as e:
        store.update("alice", role="admin")
    assert e.value.kind == "unavailable"
    with pytest.raises(DaemonError):
        registry.access().sync.create_acl("user", "alice", "admin", "vm", "web")
    assert store.get("alice")["role"] == "viewer"
    assert registry.access().sync.acl() == []


def test_a_write_goes_to_the_daemon_with_the_methods_own_arguments(database, monkeypatch):
    from app.repositories import registry

    asked = []
    call = privileged._call
    monkeypatch.setattr(
        privileged, "_call", lambda op, user, timeout, args: asked.append((op, args)) or call(op, user, timeout, args)
    )
    group_id = registry.access().sync.create_group("ops")
    registry.access().sync.add_group_member(group_id, "alice")
    assert asked == [
        ("access.group_create", {"name": "ops"}),
        ("access.group_member_add", {"group_id": group_id, "username": "alice"}),
    ]


@pytest.mark.parametrize(
    ("op", "args"),
    [
        (
            "access.acl_create",
            {
                "subject_type": "everyone",
                "subject_id": "x",
                "role": "admin",
                "resource_type": "vm",
                "resource_id": "web",
            },
        ),
        (
            "access.acl_create",
            {"subject_type": "user", "subject_id": "x", "role": "admin", "resource_type": "host", "resource_id": "/"},
        ),
        ("access.user_update", {"username": "alice", "role": "admin", "sql": "1=1"}),
        ("access.user_create", {"username": "", "hashed_password": "h", "role": "admin"}),
        ("access.group_member_add", {"group_id": 0, "username": "alice"}),
    ],
)
def test_an_operation_takes_its_own_fields_only(op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "invalid_arguments"


def test_a_name_taken_is_still_already_exists(database):
    from app.domain.common import AlreadyExists
    from app.repositories import registry

    registry.access().sync.create_group("ops")
    with pytest.raises(AlreadyExists):
        registry.access().sync.create_group("ops")


def test_the_second_factor_is_made_and_checked_by_the_daemon(database, make_user):
    """Its secret is sealed by the daemon and stored sealed; a code is checked there, and accepted once."""
    from app.repositories import registry

    make_user("bob", "viewer")
    secret = privileged.run("access.totp_start", username="bob")["secret"]
    stored = registry.accounts().sync.get("bob")["totp_secret"]
    assert stored and secret not in stored
    code = pyotp.TOTP(secret).now()
    assert privileged.run("access.totp_check", username="bob", code=code) == {"valid": True}
    assert privileged.run("access.totp_check", username="bob", code=code) == {"valid": False}
    assert privileged.run("access.totp_check", username="bob", code="000000") == {"valid": False}
    with pytest.raises(DaemonError) as e:
        privileged.run("access.totp_check", username="nobody", code=code)
    assert e.value.kind == "not_found"


def test_a_rename_moves_no_permission_itself():
    """The renames' own lists (the rows a VM's or a container's rename moves, the labels) name none of these tables:
    their part is the daemon's (SqliteAccessStore.move_vm, move_container, rename_label)."""
    named = {t for t, _c in (*renames._VM_COLUMNS, *renames._CONTAINER_COLUMNS, *renames._NODE_COLUMNS)}
    named |= {t for t, _c in renames.LABELS.values()}
    assert not named & set(TABLES)
    source = inspect.getsource(renames)
    assert not re.search(r'"(?:acl|pool_members|groups|pools|custom_roles|api_tokens|users)"', source)


def test_a_renamed_vm_keeps_its_pools_and_acls(database, make_user, monkeypatch):
    from app.core import renaming
    from app.repositories import registry

    asked = []
    call = privileged._call
    monkeypatch.setattr(
        privileged, "_call", lambda op, user, timeout, args: asked.append(op) or call(op, user, timeout, args)
    )
    store = registry.access().sync
    pool = store.create_pool("prod", "")
    store.add_pool_member(pool, "web")
    store.create_acl("user", "alice", "admin", "vm", "web")
    renaming.vm_records("web", "web2")
    assert "access.vm_moved" in asked
    assert [a["resource_id"] for a in store.acl("vm")] == ["web2"]
    group = store.create_group("ops")
    with pytest.raises(renaming.LabelTaken):
        store.create_group("dev") and renaming.rename_label("group", group, "dev")
    assert renaming.rename_label("group", group, "ops2") == "ops"
