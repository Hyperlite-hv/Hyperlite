"""NFS pools: the protocol version is chosen at creation and always given explicitly to mount.nfs."""

import xml.etree.ElementTree as ET

import pytest

FS = "{http://libvirt.org/schemas/storagepool/fs/1.0}"


def _options(xml):
    return [o.get("name") for o in ET.fromstring(xml).iter(f"{FS}option")]


@pytest.mark.parametrize("version", ["3", "4", "4.0", "4.1", "4.2"])
def test_the_chosen_version_is_passed_to_mount(version):
    from app.routers import storage

    payload = storage.PoolCreate(
        name="nas", type="netfs", nfs_host="127.0.0.1", nfs_export_path="/srv/share", nfs_version=version
    )
    options = _options(storage._build_pool_xml(payload, "/var/lib/hyperlite/nas"))
    assert f"vers={version}" in options and "addr=127.0.0.1" in options
    assert sum(o.startswith("vers=") for o in options) == 1


def test_without_a_choice_the_previous_version_is_kept():
    from app.routers import storage

    payload = storage.PoolCreate(name="nas", type="netfs", nfs_host="127.0.0.1", nfs_export_path="/srv/share")
    assert "vers=4.2" in _options(storage._build_pool_xml(payload, "/var/lib/hyperlite/nas"))


@pytest.mark.parametrize("version", ["2", "5", "4.2,nolock", "", "3;rm"])
def test_other_versions_are_refused(version):
    from pydantic import ValidationError

    from app.routers import storage

    with pytest.raises(ValidationError):
        storage.PoolCreate(name="nas", type="netfs", nfs_host="h", nfs_export_path="/s", nfs_version=version)


class _Pool:
    def __init__(self, xml):
        self.xml = xml

    def XMLDesc(self, flags=0):
        return self.xml


def test_the_version_is_read_back_from_the_pool_definition():
    from app.routers import storage

    # the form libvirt returns: the fs namespace as a prefix on the root
    defined = (
        "<pool type='netfs' xmlns:fs='http://libvirt.org/schemas/storagepool/fs/1.0'><name>nas</name>"
        "<source><host name='nas.lan'/><dir path='/srv'/><format type='nfs'/></source>"
        "<target><path>/mnt</path></target>"
        "<fs:mount_opts><fs:option name='addr=10.0.0.5'/><fs:option name='vers=3'/></fs:mount_opts></pool>"
    )
    assert storage._nfs_version(_Pool(defined)) == "3"
    assert storage._nfs_version(_Pool("<pool type='netfs'><name>x</name></pool>")) is None
