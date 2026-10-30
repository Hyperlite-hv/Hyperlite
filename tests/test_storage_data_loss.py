"""Two ways storage lost data (found by ECC's security-reviewer, in a sandbox): retention deleting the backups a site
recovery imported, and a live disk move removing the file the VM had just switched to."""

import libvirt
import pytest

from app.core import backups, disk_move


def test_retention_never_counts_nor_deletes_an_imported_backup(database, tmp_path, monkeypatch):
    monkeypatch.setattr(backups.backup_use, "holder", lambda backup_id: None)
    paths = {}
    with database.get_conn() as db:
        for i, imported in ((1, "site-a"), (2, None), (3, None)):
            path = tmp_path / f"web-{i}"
            path.mkdir()
            paths[i] = path
            db.execute(
                "INSERT INTO backups (id, vm_name, path, mode, created_at, status, imported_from) "
                "VALUES (?, 'web', ?, 'cold', ?, 'succeeded', ?)",
                (i, str(path), f"2026-10-0{i}T00:00:00+00:00", imported),
            )
        db.commit()
    backups._apply_retention("web", {"last": 1, "daily": 0, "weekly": 0, "monthly": 0})
    # The other site's backup stays, whatever its age; of this installation's own, the last one is kept.
    assert paths[1].exists() and not paths[2].exists() and paths[3].exists()


class LiveDomain:
    def __init__(self):
        self.aborted = []

    def blockCopy(self, dev, xml, params, flags):
        pass

    def blockJobInfo(self, dev, flags):
        return {"cur": 10, "end": 10}

    def blockJobAbort(self, dev, flags):
        self.aborted.append(flags)


def test_a_failure_after_the_pivot_keeps_both_files(tmp_path, monkeypatch):
    src, dest = tmp_path / "src.qcow2", tmp_path / "dest.qcow2"
    src.write_bytes(b"source")

    def create(argv, **kw):
        dest.write_bytes(b"what the VM now writes to")

    def refused(domain, dev, path):
        raise libvirt.libvirtError("the definition changed meanwhile")

    monkeypatch.setattr(disk_move.subprocess, "run", create)
    monkeypatch.setattr(disk_move, "_disk_config_path", lambda domain, dev: None)
    monkeypatch.setattr(disk_move, "_point_config_at", refused)
    how = {"live": True, "source": str(src), "dest": str(dest), "capacity": 1, "source_pool": None, "dest_pool": None}
    domain = LiveDomain()
    with pytest.raises(disk_move.Pivoted, match="now runs on"):
        disk_move.move(domain, "vda", how, delete_source=True)
    assert dest.exists() and src.exists()  # dest is the live disk; src is what a restart would read
    assert domain.aborted == [libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT]  # pivoted once, never cancelled after
