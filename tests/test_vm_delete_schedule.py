"""Deleting a VM removes its backup schedule (it stayed, and failed at every run); its backups are kept."""

import libvirt

from app.core import vm_delete
from app.routers.vms import lifecycle
from app.services import backup_service


class _Domain:
    def XMLDesc(self, *_):
        return "<domain><name>web</name><devices/></domain>"

    def isActive(self):
        return False

    def undefineFlags(self, flags):
        self.undefined = True


class _Conn:
    """This node's libvirt as hyperlite-daemon sees it: the VM, no other, no pool."""

    def __init__(self, domain):
        self.domain = domain

    def lookupByName(self, name):
        return self.domain

    def listAllDomains(self, flags=0):
        return []

    def listAllStoragePools(self, flags=0):
        return []

    def storageVolLookupByPath(self, path):
        raise libvirt.libvirtError("no storage vol with matching path")


def test_deleting_a_vm_removes_its_backup_schedule(database, daemon_libvirt):
    backup_service.set_schedule("web", "daily", "02:00", "/srv/b", 7, None, None, None, "2030-01-01T02:00:00")
    backup_service.set_schedule("db", "daily", "02:00", "/srv/b", 7, None, None, None, "2030-01-01T02:00:00")
    with database.get_conn() as conn:
        conn.execute(
            "INSERT INTO backups (vm_name, path, mode, created_at, status) VALUES ('web', '/srv/b/web/1', 'cold', 'x', 'succeeded')"
        )
        conn.commit()
    domain = _Domain()
    lifecycle._perform_vm_deletion(daemon_libvirt(_Conn(domain)), domain, "web")
    assert domain.undefined
    assert backup_service.schedule_of("web") is None
    assert backup_service.schedule_of("db") is not None
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM backups WHERE vm_name = 'web'").fetchone()[0] == 1


def test_the_vm_directory_goes_with_its_last_backup(database, tmp_path):
    ids = []
    for stamp in ("1", "2"):
        (tmp_path / "web" / stamp).mkdir(parents=True)
        (tmp_path / "web" / stamp / "sda.qcow2").write_text("x")
        with database.get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO backups (vm_name, path, mode, created_at, status) VALUES ('web', ?, 'cold', 'x', 'succeeded')",
                (str(tmp_path / "web" / stamp),),
            )
            conn.commit()
            ids.append(cur.lastrowid)
    backup_service.delete(ids[0])
    assert (tmp_path / "web").is_dir() and not (tmp_path / "web" / "1").exists()
    backup_service.delete(ids[1])
    assert not (tmp_path / "web").exists() and tmp_path.is_dir()


def test_the_pools_holding_the_deleted_files_are_refreshed(database, tmp_path, monkeypatch, daemon_libvirt):
    # The files go behind libvirt's back: without a refresh the pool kept listing them (seen on the provider's
    # acceptance runner: a deleted VM's disk and drive still in `virsh vol-list default`).
    disk = tmp_path / "web.qcow2"
    disk.write_text("x")

    class _WithDisk(_Domain):
        def XMLDesc(self, *_):
            return (
                f"<domain><name>web</name><devices><disk type='file' device='disk'><source file='{disk}'/>"
                "<target dev='sda'/></disk></devices></domain>"
            )

    refreshed = []
    monkeypatch.setattr(vm_delete, "IMAGES_DIR", tmp_path)  # a disk Hyperlite made: in its images directory
    monkeypatch.setattr(vm_delete, "refresh_pools_for_paths", lambda conn, paths: refreshed.append(list(paths)))
    domain = _WithDisk()
    lifecycle._perform_vm_deletion(daemon_libvirt(_Conn(domain)), domain, "web")
    assert not disk.exists() and refreshed == [[disk]]
