"""hyperlite-daemon's work on an existing container (app/core/container_ops.py): its filesystem is found in its
definition and accepted in Hyperlite's container directories only, so root never deletes, renames or writes into a
directory the API's records, or a definition written by hand, would name."""

from unittest.mock import Mock

import libvirt
import pytest

from app.core import container_ops, privileged
from app.daemon.registry import DaemonError


class _Dom:
    def __init__(self, name, source, active=False):
        self._name, self.source, self.active, self.undefined = name, source, active, False

    def name(self):
        return self._name

    def XMLDesc(self, flags=0):
        return (
            f"<domain type='lxc'><name>{self._name}</name><devices><filesystem type='mount'>"
            f"<source dir='{self.source}'/><target dir='/'/></filesystem></devices></domain>"
        )

    def isActive(self):
        return self.active

    def autostart(self):
        return 0

    def undefine(self):
        self.undefined = True


class _Lxc:
    def __init__(self, *domains):
        self.domains = {d.name(): d for d in domains}

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def defineXML(self, xml):
        return Mock()


def _pool(target):
    pool = Mock()
    pool.XMLDesc.return_value = f"<pool type='dir'><target><path>{target}</path></target></pool>"
    return pool


@pytest.fixture()
def node(tmp_path, monkeypatch, daemon_libvirt, daemon_lxc):
    default, pool_dir, elsewhere = tmp_path / "containers", tmp_path / "fast", tmp_path / "etc"
    for directory in (default / "web" / "etc", pool_dir / "hyperlite-containers" / "db" / "etc", elsewhere):
        directory.mkdir(parents=True)
    monkeypatch.setattr(container_ops, "CONTAINERS_DIR", default)
    daemon_libvirt(Mock(**{"listAllStoragePools.return_value": [_pool(pool_dir)]}))
    lxc = daemon_lxc(
        _Lxc(
            _Dom("web", default / "web"),
            _Dom("db", pool_dir / "hyperlite-containers" / "db"),
            _Dom("evil", elsewhere),
            _Dom("other", default / "web"),  # another container's directory
        )
    )
    return {"lxc": lxc, "default": default, "pool": pool_dir, "elsewhere": elsewhere}


def test_a_filesystem_outside_the_container_directories_is_never_deleted(node):
    done = privileged.run("container.delete", container="evil")
    assert node["elsewhere"].is_dir() and node["lxc"].domains["evil"].undefined
    assert done["filesystem_kept"] == str(node["elsewhere"])
    done = privileged.run("container.delete", container="other")
    assert (node["default"] / "web").is_dir(), "another container's directory is not this one's to delete"


def test_a_filesystem_in_the_default_place_or_a_directory_pool_goes_with_its_container(node):
    assert privileged.run("container.delete", container="web")["filesystem_kept"] is None
    assert privileged.run("container.delete", container="db")["filesystem_kept"] is None
    assert not (node["default"] / "web").exists()
    assert not (node["pool"] / "hyperlite-containers" / "db").exists()


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("container.rename", {"new_name": "shop"}),
        ("container.update", {"dns": ["9.9.9.9"]}),
    ],
)
def test_root_never_writes_into_a_filesystem_hyperlite_does_not_manage(node, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, container="evil", **args)
    assert e.value.kind == "refused" and "not in a container directory" in e.value.detail
    assert not (node["elsewhere"] / "resolv.conf").exists()


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("container.delete", {"container": "web", "rootfs": "/etc"}),
        ("container.rename", {"container": "web", "new_name": "x" * 65}),
        ("container.update", {"container": "web", "memory_mb": 64}),
        ("container.stop", {"container": "web", "run_id": -1}),
    ],
)
def test_the_fields_are_checked_by_the_daemon(node, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "invalid_arguments"


@pytest.mark.parametrize(
    ("op", "args"),
    [
        ("container.create", {"spec": {"name": "new", "rootfs": "/etc"}}),
        ("container.create", {"spec": {"name": "new", "network": "lan", "xml": "<domain/>"}}),
        ("container.clone", {"container": "web", "new_name": "web2", "app": {"args": ["sh"], "init": "/bin/sh"}}),
        ("container.clone", {"container": "web", "new_name": "web2", "app": {"args": []}}),
    ],
)
def test_a_creation_or_a_clone_takes_a_specification_never_a_path_or_a_definition(node, op, args):
    with pytest.raises(DaemonError) as e:
        privileged.run(op, **args)
    assert e.value.kind == "invalid_arguments"


def test_a_clone_is_made_from_a_stopped_container_in_a_directory_hyperlite_manages(node):
    node["lxc"].domains["web"].active = True
    with pytest.raises(DaemonError) as e:
        privileged.run("container.clone", container="web", new_name="web2")
    assert e.value.kind == "in_use"
    with pytest.raises(DaemonError) as e:
        privileged.run("container.clone", container="evil", new_name="evil2")
    assert e.value.kind == "refused" and not (node["elsewhere"].parent / "evil2").exists()
