"""Deleting a VM removes its backup schedule (it stayed, and failed at every run); its backups are kept."""

from app.routers.vms import lifecycle
from app.services import backup_service


class _Domain:
    def XMLDesc(self, *_):
        return "<domain><name>web</name><devices/></domain>"

    def undefineFlags(self, flags):
        self.undefined = True


def test_deleting_a_vm_removes_its_backup_schedule(database):
    backup_service.set_schedule("web", "quotidien", "02:00", "/srv/b", 7, None, None, None, "2030-01-01T02:00:00")
    backup_service.set_schedule("db", "quotidien", "02:00", "/srv/b", 7, None, None, None, "2030-01-01T02:00:00")
    with database.get_conn() as conn:
        conn.execute(
            "INSERT INTO backups (vm_name, chemin, mode, cree_le, statut) VALUES ('web', '/srv/b/web/1', 'froid', 'x', 'termine')"
        )
        conn.commit()
    domain = _Domain()
    lifecycle._perform_vm_deletion(None, domain, "web")
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
                "INSERT INTO backups (vm_name, chemin, mode, cree_le, statut) VALUES ('web', ?, 'froid', 'x', 'termine')",
                (str(tmp_path / "web" / stamp),),
            )
            conn.commit()
            ids.append(cur.lastrowid)
    backup_service.delete(ids[0])
    assert (tmp_path / "web").is_dir() and not (tmp_path / "web" / "1").exists()
    backup_service.delete(ids[1])
    assert not (tmp_path / "web").exists() and tmp_path.is_dir()
