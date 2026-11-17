"""Every operation hyperlite-daemon guards with a privilege on an object (a container, a storage pool, a network, a
backup store, a list of VMs) or on an area: refused to an account without it, allowed to one that holds it on that very
object. The daemon's check is the one that holds when the API is not to be trusted."""

from types import SimpleNamespace

import pytest

import app.daemon.ops  # noqa: F401  (registers the operations)
from app.core import permissions
from app.daemon.registry import NEEDS

KINDS = ("container", "storage", "network", "backup_store", "vms", "area")
GUARDED = sorted(op for op, need in NEEDS.items() if getattr(need, "kind", None) in KINDS)


def test_every_kind_is_guarded_somewhere():
    assert {NEEDS[op].kind for op in GUARDED} >= {"container", "storage", "backup_store", "vms", "area"}


@pytest.fixture()
def holds(monkeypatch):
    """The account holds every privilege on the object named "mine" (and its VMs), and nothing anywhere else."""
    held = {"area": False}
    monkeypatch.setattr(permissions, "has_privilege", lambda user, vm, p: vm == "mine")
    monkeypatch.setattr(permissions, "has_container_privilege", lambda user, ct, p: ct == "mine")
    monkeypatch.setattr(permissions, "has_scoped_privilege", lambda user, kind, ident, p: ident == "mine")
    monkeypatch.setattr(permissions, "has_area_privilege", lambda user, p: held["area"])
    return held


@pytest.mark.parametrize("op", GUARDED)
def test_an_object_privilege_holds_on_that_object_only(op, holds):
    need = NEEDS[op]
    ana = {"username": "ana", "role": "viewer"}
    if need.kind == "area":
        assert need.refusal(ana, SimpleNamespace()) is not None
        holds["area"] = True
        assert need.refusal(ana, SimpleNamespace()) is None
        return
    mine = ["mine"] if need.kind == "vms" else "mine"
    other = ["mine", "theirs"] if need.kind == "vms" else "theirs"
    assert need.refusal(ana, SimpleNamespace(**{need.arg: other})) is not None, f"{op} let in without the privilege"
    assert need.refusal(ana, SimpleNamespace(**{need.arg: mine})) is None, f"{op} refused its holder"
