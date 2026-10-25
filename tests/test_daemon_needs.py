"""What each operation needs of the person who asks, checked by hyperlite-daemon (app/daemon/needs.py;
docs/design/privilege-separation.md, section 14, 4d), as pvedaemon checks each method's permissions against user.cfg
whatever pveproxy says. The routes keep their own checks; the daemon's is the one that holds."""

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import peer_auth, privileged, security
from app.daemon import needs
from app.daemon.registry import NEEDS, OPERATIONS, Context, DaemonError, dispatch


def _as(username):
    """The calls of this test carry `username`'s session, as a request of that account does."""
    peer_auth.remember(security.create_access_token({"sub": username}))


def _from_socket(op, uid, proof=None, **args):
    """A request as the socket hands it over: another process, of `uid`."""
    return dispatch({"op": op, "args": args}, Context("someone", None, 1, uid, proof=proof))


def test_an_administrator_may_run_any_operation(make_user):
    make_user("ana", "admin")
    _as("ana")
    assert privileged.run("system.ping") is not None
    with pytest.raises(DaemonError) as e:
        privileged.run("network.delete", network="nope")  # refused by the operation, not by its needs
    assert e.value.kind != "forbidden"


def test_a_viewer_is_refused_an_administrators_operation(make_user):
    make_user("vic", "viewer")
    _as("vic")
    for op, args in (
        ("network.delete", {"network": "lan"}),
        ("console.host_shell", {}),
        (
            "access.acl_create",
            {
                "subject_type": "user",
                "subject_id": "vic",
                "role": "manager",
                "resource_type": "vm",
                "resource_id": "web",
            },
        ),
        ("access.user_update", {"username": "vic", "role": "admin"}),
    ):
        with pytest.raises(DaemonError) as e:
            privileged.run(op, **args)
        assert e.value.kind == "forbidden", op


def test_a_privilege_on_a_vm_allows_that_vm_only(make_user, database):
    from app.core import permissions

    make_user("oli", "viewer")
    permissions.create_acl("user", "oli", "operator", "vm", "web")
    _as("oli")
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.power", vm="db", action="start")
    assert e.value.kind == "forbidden" and "'db'" in e.value.detail
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.power", vm="web", action="start")
    assert e.value.kind != "forbidden"  # its needs met; the VM itself is not in this test's libvirt
    with pytest.raises(DaemonError) as e:
        privileged.run("vm.snapshot_create", vm="web", name="s1")  # vm.snapshot: a manager's, not an operator's
    assert e.value.kind == "forbidden"


def test_an_account_acts_on_itself_only(make_user):
    make_user("vic", "viewer")
    make_user("bob", "viewer")
    _as("vic")
    assert "secret" in privileged.run("access.totp_start", username="vic")
    with pytest.raises(DaemonError) as e:
        privileged.run("access.totp_start", username="bob")
    assert e.value.kind == "forbidden"
    with pytest.raises(DaemonError) as e:
        privileged.run("access.user_update", username="vic", role="admin")  # a role is an administrator's
    assert e.value.kind == "forbidden"


def test_without_a_proof_only_this_nodes_services_are_trusted():
    """Root (hyperlite-scheduler, HA's services; the API until step 4e) and the daemon's own code; anyone else needs
    the proof of the account that asks."""
    answer = _from_socket("network.delete", uid=33, network="lan")  # www-data, without a proof
    assert not answer["ok"] and answer["error"] == "forbidden"
    answer = _from_socket("system.ping", uid=33)  # what needs no proof
    assert answer["ok"]


def test_a_credential_that_proves_nothing_is_refused():
    answer = _from_socket("network.delete", uid=0, proof="not-a-token", network="lan")
    assert not answer["ok"] and answer["error"] == "forbidden"


def test_root_through_hyperlite_admin_is_an_administrator(database):
    """root of a node has no account row, as root@pam on Proxmox VE: its session is an administrator's."""
    from app.core import sign_in

    token = sign_in.local_root()["token"]
    proof = Context(None, None, 1, 33, proof=token)
    assert needs.check("network.delete", needs.ADMIN, proof, None).startswith(security.LOCAL_ROOT_PREFIX)


def test_a_refusal_is_the_routes_403():
    assert privileged.http_error(DaemonError("forbidden", "Only an administrator may do this")).status_code == 403


def test_every_operation_says_what_it_needs_or_is_an_administrators():
    """An operation without needs= is an administrator's: what a person without that role may ask is listed."""
    for op in OPERATIONS:
        need = NEEDS.get(op, needs.ADMIN)
        assert isinstance(need, needs.Need), op
    public = sorted(op for op, need in NEEDS.items() if need.kind == "public")
    # The copy to and from hyperlite-cfs copies only what the node holds (app/daemon/ops/cfs.py).
    copies = {"system.ping", "cfs.catch_up", "cfs.confirm"}
    assert all(op.startswith("auth.") or op in copies for op in public), public
