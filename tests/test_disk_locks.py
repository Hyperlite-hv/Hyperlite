"""Disk locks across nodes (docs/design/ha-automatic.md, 4.7, #333 step 3): a share that keeps its locks on the node that
took them lets a second copy of a VM open the disk; HA refuses it, and a definition with QEMU's locks turned off."""

import pytest

from app.core import disk_locks, ha

# Real lines of /proc/self/mountinfo (an NFS 4.2 share of the lab, mounted three ways, an SMB share, the root).
MOUNTINFO = """\
22 1 254:0 / / rw,relatime shared:1 - ext4 /dev/vda1 rw
40 22 0:52 / /mnt/share rw,relatime shared:20 - nfs4 192.0.2.1:/srv/share rw,vers=4.2,rsize=262144,wsize=262144,namlen=255,hard,proto=tcp,timeo=600,retrans=2,sec=sys,clientaddr=192.0.2.2,local_lock=none,addr=192.0.2.1
41 22 0:53 / /mnt/local\\040share rw,relatime shared:21 - nfs4 192.0.2.1:/srv/share rw,vers=4.2,local_lock=all,addr=192.0.2.1
42 22 0:54 / /mnt/v3 rw,relatime shared:22 - nfs 192.0.2.1:/srv/v3 rw,vers=3,nolock,proto=tcp,local_lock=all,addr=192.0.2.1
43 22 0:55 / /mnt/smb rw,relatime shared:23 - cifs //nas/vms rw,vers=3.1.1,cache=strict,nobrl,soft
44 22 0:56 / /mnt/smb-ok rw,relatime shared:24 - cifs //nas/vms rw,vers=3.1.1,cache=strict,soft
"""


@pytest.fixture()
def mounts(tmp_path, monkeypatch):
    path = tmp_path / "mountinfo"
    path.write_text(MOUNTINFO)
    monkeypatch.setattr(disk_locks, "MOUNTINFO", path)


@pytest.mark.parametrize(
    ("disk", "reason"),
    [
        ("/mnt/share/web.qcow2", None),  # NFSv4 locks are the server's: what the lab checked
        ("/mnt/local share/web.qcow2", "local_lock=all"),  # an escaped mount point
        ("/mnt/v3/web.qcow2", "nolock"),
        ("/mnt/smb/web.qcow2", "nobrl"),
        ("/mnt/smb-ok/web.qcow2", None),
        ("/var/lib/libvirt/images/web.qcow2", None),  # a local disk: no other node opens it
    ],
)
def test_a_share_that_keeps_its_locks_on_the_node_is_named(mounts, disk, reason):
    found = disk_locks.local_only(disk)
    assert (found is None) if reason is None else (reason in found)


def test_the_longest_mount_point_holds_the_disk(mounts):
    assert disk_locks.mount_of("/mnt/smb-ok/vm/disk.qcow2")[0] == "/mnt/smb-ok"
    assert disk_locks.mount_of("/mnt/smbx/disk.qcow2")[0] == "/"


class Domain:
    def __init__(self, xml):
        self.xml = xml

    def XMLDesc(self, flags=0):
        return self.xml

    def setAutostart(self, on):
        pass

    def isActive(self):
        return False  # stopped: admission control counts it once it starts


def disk_xml(path, driver="<driver name='qemu' type='qcow2'/>"):
    return f"<domain><name>web</name><devices><disk type='file' device='disk'>{driver}<source file='{path}'/></disk></devices></domain>"


@pytest.fixture()
def protecting(database, mounts, monkeypatch):
    """enable_protection on this node, its shared-storage check passed: what it decides from the locks."""

    def enable(xml):
        class Conn:
            def lookupByName(self, name):
                return Domain(xml)

            def close(self):
                pass

        monkeypatch.setattr(ha, "open_conn", lambda key=None, **_k: Conn())
        monkeypatch.setattr(ha, "uses_shared_storage", lambda *a: True)
        ha.enable_protection("web", None, "admin")
        return ha.get_protected("web")

    return enable


def test_a_vm_on_a_share_that_locks_across_nodes_is_protected(protecting):
    assert protecting(disk_xml("/mnt/share/web.qcow2"))["vm_name"] == "web"


@pytest.mark.parametrize(
    ("xml", "text"),
    [
        (disk_xml("/mnt/local share/web.qcow2"), "local_lock=all"),
        (disk_xml("/mnt/smb/web.qcow2"), "nobrl"),
        (
            disk_xml("/mnt/share/web.qcow2").replace(
                "</devices>",
                "</devices><qemu:commandline xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'><qemu:arg value='-set'/><qemu:arg value='drive.disk0.file.locking=off'/></qemu:commandline>",
            ),
            "locking=off",
        ),
    ],
)
def test_a_vm_nothing_would_stop_a_second_copy_of_is_refused(protecting, xml, text):
    with pytest.raises(RuntimeError, match="HA protection impossible") as err:
        protecting(xml)
    assert text in str(err.value) and ha.get_protected("web") is None
