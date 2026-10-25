"""The permissions, written by hyperlite-daemon only (docs/design/privilege-separation.md, section 14.1, step 4b): the
accounts, API tokens, security keys, second factors, revoked sessions, groups, roles, resource pools and ACLs, as
pvedaemon writes Proxmox VE's user.cfg and priv/.

Each operation is one repository method marked @daemon_write (app/repositories/daemon_write.py), run here with the
arguments its own model checked: names, ids and hashes with their limits, never SQL or a path. It answers
{"result", "outbox"}: the method's result, and the cluster's outbox entries its write made. What needs the node's
secrets is done here too: a second factor's secret is made and sealed, and a code checked, by the daemon."""

from typing import Annotated, Literal

from pydantic import Field

from app.daemon.needs import ADMIN, ANYONE, by_rule, on_vm, own
from app.daemon.registry import Args, DaemonError, operation

Name = Annotated[str, Field(min_length=1, max_length=256)]  # an account, a group, a pool, a VM
Role = Annotated[str, Field(min_length=1, max_length=128)]
Id = Annotated[int, Field(ge=1)]
Hash = Annotated[str, Field(min_length=1, max_length=1024)]
Sealed = Annotated[str, Field(min_length=1, max_length=8192)]
Time = Annotated[str, Field(min_length=1, max_length=64)]  # ISO 8601
Seconds = Annotated[int, Field(ge=0)]
Text = Annotated[str, Field(max_length=2048)]
Privileges = Annotated[str, Field(max_length=4096)]  # comma-separated, as the table stores them
Dn = Annotated[str, Field(min_length=1, max_length=1024)]


def _store(kind):
    from app.repositories import registry

    return {"accounts": registry.accounts, "identity": registry.identity, "access": registry.access}[kind]().sync


def _write(kind, method, args):
    import sqlite3

    from app.domain.common import AlreadyExists
    from app.repositories.cfs import confirm, ids

    # The outbox entries the write makes go back to the API, whose request answers once the cluster took them
    # (docs/design/confirmed-writes.md), as when it wrote them itself.
    request, token = confirm.begin()
    try:
        result = getattr(_store(kind), method)(**args.model_dump())
    except (AlreadyExists, sqlite3.IntegrityError) as e:
        raise DaemonError("in_use", str(e)) from None
    except ids.NoId:
        raise DaemonError("unavailable", ids.NO_ID_DETAIL) from None
    finally:
        confirm.end(token)
    return {"result": result, "outbox": sorted(request.ids)}


# --- What a person needs for each (section 14, 4d): an administrator, unless the account acts on itself ---


def _own_password(user, args):
    """A new password for the account itself; a role is an administrator's to change."""
    if args.role is not None:
        return "Only an administrator changes a role"
    return None if args.username == user["username"] else "Only the account itself or an administrator may do this"


def _may_allocate_in_pool(user, args):
    """A VM put in a resource pool by the account that made it there (vm.allocate on that pool)."""
    from app.core import permissions

    if permissions.has_scoped_privilege(user, "pool", args.pool_id, "vm.allocate"):
        return None
    return f"Insufficient rights on pool {args.pool_id} (required privilege: vm.allocate)"


def _own_token_label(user, args):
    """An API token renamed by its owner; any other label is an administrator's."""
    if args.kind == "api_token" and args.owner == user["username"]:
        return None
    return "Only an administrator may do this"


def _register(op, kind, method, model, needs):
    @operation(f"access.{op}", model, needs=needs)
    def run(args, ctx):
        return _write(kind, method, args)

    run.__name__ = op
    return run


# --- Accounts ---


class UserCreate(Args):
    username: Name
    hashed_password: Hash
    role: Role
    auth_source: Literal["local", "ldap", "sso"] | None = None
    ldap_dn: Dn | None = None


class UserUpdate(Args):
    username: Name
    role: Role | None = None
    hashed_password: Hash | None = None
    changed_at: Seconds | None = None


class UserLdap(Args):
    username: Name
    role: Role
    dn: Dn


class User(Args):
    username: Name


class UserLogin(Args):
    username: Name
    at: Time


class TotpSecret(Args):
    username: Name
    sealed: Sealed


class TotpStep(Args):
    username: Name
    step: Seconds


class TokenCreate(Args):
    username: Name
    name: Name
    token_hash: Hash
    created_at: Time
    expires_at: Time | None
    kind: Annotated[str, Field(min_length=1, max_length=32)]


class TokenRevoke(Args):
    username: Name
    token_id: Id


class TokenUsed(Args):
    token_hash: Hash
    at: Time


class SessionRevoke(Args):
    jti: Annotated[str, Field(min_length=1, max_length=256)]
    expires_at: Seconds
    now: Seconds


_register("user_create", "accounts", "create", UserCreate, needs=ADMIN)
_register("user_update", "accounts", "update", UserUpdate, needs=by_rule(_own_password))
_register("user_ldap", "accounts", "set_ldap", UserLdap, needs=ADMIN)
_register("user_delete", "accounts", "delete", User, needs=ADMIN)
_register("user_login", "accounts", "record_login", UserLogin, needs=ANYONE)
_register("user_must_change", "accounts", "flag_must_change", User, needs=ADMIN)
_register("totp_secret", "accounts", "set_totp_secret", TotpSecret, needs=own())
_register("totp_enable", "accounts", "enable_totp", User, needs=own())
_register("totp_disable", "accounts", "disable_totp", User, needs=own())
_register("totp_step", "accounts", "claim_totp_step", TotpStep, needs=own())
_register("totp_reseal", "accounts", "replace_totp_secret", TotpSecret, needs=ADMIN)
_register("token_create", "accounts", "create_token", TokenCreate, needs=own())
_register("token_revoke", "accounts", "revoke_token", TokenRevoke, needs=own())
_register("token_revoke_all", "accounts", "revoke_all_tokens", User, needs=own())
_register("token_used", "accounts", "touch_token", TokenUsed, needs=ANYONE)
_register("session_revoke", "accounts", "revoke_session", SessionRevoke, needs=ANYONE)


# --- Security keys and the accounts of a directory or an IdP ---


class KeyDelete(Args):
    username: Name
    key_id: Id


class KeyAdd(Args):
    username: Name
    name: Name
    credential_id: Annotated[str, Field(min_length=1, max_length=1024)]
    public_key: Annotated[str, Field(min_length=1, max_length=4096)]
    sign_count: Seconds
    rp_id: Annotated[str, Field(min_length=1, max_length=256)]
    created_at: Time


class KeyUsed(Args):
    key_id: Id
    sign_count: Seconds
    used_at: Time


class ProvisionSso(Args):
    username: Name
    role: Role
    subject: Annotated[str, Field(max_length=512)] | None


class ProvisionLdap(Args):
    username: Name
    dn: Dn
    role: Role


_register("key_delete", "identity", "delete_key", KeyDelete, needs=own())
_register("key_delete_all", "identity", "delete_all_keys", User, needs=own())
_register("key_add", "identity", "add_credential", KeyAdd, needs=own())
_register("key_used", "identity", "record_key_use", KeyUsed, needs=ANYONE)
_register("provision_sso", "identity", "provision_sso_user", ProvisionSso, needs=ADMIN)
_register("provision_ldap", "identity", "provision_ldap_user", ProvisionLdap, needs=ADMIN)


# --- Roles, groups, resource pools, ACLs ---


class RoleCreate(Args):
    name: Name
    privileges: Privileges


class RolePrivileges(Args):
    role_id: Id
    privileges: Privileges


class RoleDelete(Args):
    role_key: Role
    role_id: Id


class GroupCreate(Args):
    name: Name


class GroupId(Args):
    group_id: Id


class GroupMember(Args):
    group_id: Id
    username: Name


class PoolCreate(Args):
    name: Name
    description: Text | None = None


class PoolDescription(Args):
    pool_id: Id
    description: Text | None


class PoolId(Args):
    pool_id: Id


class PoolMember(Args):
    pool_id: Id
    vm_name: Name


class PoolMemberRename(Args):
    old_vm_name: Name
    new_vm_name: Name


class Vm(Args):
    vm_name: Name


class AclCreate(Args):
    subject_type: Literal["user", "group"]
    subject_id: Name
    role: Role
    resource_type: Literal["vm", "pool", "container", "storage", "network", "backup_store"]
    resource_id: Name


class AclId(Args):
    acl_id: Id


_register("role_create", "access", "create_custom_role", RoleCreate, needs=ADMIN)
_register("role_privileges", "access", "set_custom_role_privileges", RolePrivileges, needs=ADMIN)
_register("role_delete", "access", "delete_custom_role", RoleDelete, needs=ADMIN)
_register("group_create", "access", "create_group", GroupCreate, needs=ADMIN)
_register("group_delete", "access", "delete_group", GroupId, needs=ADMIN)
_register("group_member_add", "access", "add_group_member", GroupMember, needs=ADMIN)
_register("group_member_remove", "access", "remove_group_member", GroupMember, needs=ADMIN)
_register("pool_create", "access", "create_pool", PoolCreate, needs=ADMIN)
_register("pool_description", "access", "set_pool_description", PoolDescription, needs=ADMIN)
_register("pool_delete", "access", "delete_pool", PoolId, needs=ADMIN)
_register("pool_member_add", "access", "add_pool_member", PoolMember, needs=by_rule(_may_allocate_in_pool))
_register("pool_member_remove", "access", "remove_pool_member", PoolMember, needs=ADMIN)
_register("pool_member_rename", "access", "rename_pool_member", PoolMemberRename, needs=ADMIN)
_register("pool_vm_forget", "access", "remove_vm_from_all_pools", Vm, needs=on_vm("vm.allocate", "vm_name"))
_register("acl_create", "access", "create_acl", AclCreate, needs=ADMIN)
_register("acl_delete", "access", "delete_acl", AclId, needs=ADMIN)
_register("acl_vm_forget", "access", "delete_acl_for_vm", Vm, needs=on_vm("vm.allocate", "vm_name"))


class Moved(Args):
    old: Name
    new: Name


class RenameLabel(Args):
    kind: Literal["pool", "group", "custom_role", "api_token"]
    object_id: Id
    new: Name
    owner: Name | None = None


_register("vm_moved", "access", "move_vm", Moved, needs=ADMIN)
_register("container_moved", "access", "move_container", Moved, needs=ADMIN)
_register("rename_label", "access", "rename_label", RenameLabel, needs=by_rule(_own_token_label))


# --- Who may sign in through an IdP or a directory, and which of its groups are administrators ---

SsoColumn = Literal[
    "enabled", "issuer", "client_id", "client_secret", "redirect_uri", "scope", "group_claim", "admin_groups"
]
LdapColumn = Literal[
    "enabled",
    "url",
    "starttls",
    "verify_tls",
    "ca_cert",
    "bind_dn",
    "bind_password",
    "base_dn",
    "user_filter",
    "group_attribute",
    "admin_groups",
    "allowed_groups",
]
Setting = Annotated[str, Field(max_length=65536)] | int | bool | None


class SsoConfig(Args):
    fields: dict[SsoColumn, Setting] = Field(min_length=1)


class LdapConfig(Args):
    fields: dict[LdapColumn, Setting] = Field(min_length=1)


_register("sso_config", "identity", "save_sso_config", SsoConfig)
_register("ldap_config", "identity", "save_ldap_config", LdapConfig)

# --- What needs the node's secrets: the second factor's ---


@operation("access.totp_start", User, needs=own())
def totp_start(args, ctx):
    """A new second factor's secret, made and sealed here, stored not yet enabled: {"secret"}, answered once for the
    QR code the account scans."""
    from app.core import twofa

    secret = twofa.generate_secret()
    _store("accounts").set_totp_secret(args.username, twofa.seal_secret(secret))
    return {"secret": secret}


class TotpCheck(Args):
    username: Name
    code: Annotated[str, Field(max_length=64)]


@operation("access.totp_check", TotpCheck, needs=own())
def totp_check(args, ctx):
    """Whether `code` is the account's current one, read and opened here; each code is accepted once: {"valid"}."""
    from app.core import twofa

    row = _store("accounts").get(args.username)
    if row is None:
        raise DaemonError("not_found", f"User '{args.username}' not found")
    return {"valid": twofa.verify_code(args.username, row["totp_secret"], args.code)}
