"""hyperlite-daemon's operations of a migration between clusters (app/daemon/ops/transfer.py): what they refuse
before anything runs. They name a VM, a disk, a pool or an incoming, never a path."""

import pytest

from app.core import privileged
from app.daemon.registry import DaemonError


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("transfer.export", {"vm": "web", "dev": "../../etc/shadow"}),
        ("transfer.export", {"vm": "web", "dev": "vda", "path": "/etc/shadow"}),
        ("transfer.import", {"incoming": "../x", "dev": "vda"}),
        ("transfer.import", {"incoming": "0" * 32, "dev": "vda", "path": "/dev/sda"}),
        ("transfer.prepare", {"spec": {}, "pools": [], "allocated": []}),
        ("transfer.define", {"incoming": "0" * 32, "networks": ["x"] * 17}),
        ("transfer.abort", {"incoming": "Z" * 32}),
    ],
)
def test_arguments_out_of_bounds_are_refused(op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, user="ana", **args)
    assert e.value.kind == "invalid_arguments"


def test_an_unknown_incoming_is_not_found(tmp_path, monkeypatch):
    from app.core import libvirt_utils, site_transfer

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(site_transfer, "STATE", tmp_path)
    monkeypatch.setattr(libvirt_utils, "local_conn", Conn)
    for op, args in (("transfer.define", {}), ("transfer.abort", {}), ("transfer.import", {"dev": "vda"})):
        with pytest.raises(DaemonError) as e:
            privileged.run(op, user="ana", incoming="a" * 32, **args)
        assert e.value.kind == "not_found" and "No such incoming" in e.value.detail


def test_a_spec_the_target_refuses_makes_nothing(tmp_path, monkeypatch):
    from app.core import libvirt_utils, site_transfer

    made = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def lookupByName(self, name):
            made.append(name)

    monkeypatch.setattr(site_transfer, "STATE", tmp_path)
    monkeypatch.setattr(libvirt_utils, "local_conn", Conn)
    with pytest.raises(DaemonError) as e:
        privileged.run(
            "transfer.prepare", user="ana", spec={"name": "web", "uuid": "x"}, pools=["default"], allocated=[1]
        )
    assert e.value.kind == "refused" and "uuid" in e.value.detail and made == []
