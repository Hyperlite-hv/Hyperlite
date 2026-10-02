"""Recovery of a lost site: the backups another Hyperlite wrote to a shared storage are found, registered here and
restored as new VMs, one by one, without overwriting anything and without following links planted on the share."""

import json
import os

import libvirt
import pytest

from app.core import backup_integrity, backups, site_recovery

REAL_ALLOWED_ROOTS = site_recovery._allowed_roots


def write_backup(root, vm, stamp, source="site-a", disks=("vda",), corrupt=False):
    d = root / vm / stamp
    d.mkdir(parents=True)
    files = []
    for dev in disks:
        (d / f"{dev}.qcow2").write_bytes(b"disk " + dev.encode())
        files.append({"nom": f"{dev}.qcow2", "role": "disque", "cible": dev})
    (d / "vm-config.json").write_text(json.dumps({"vcpu": 2, "memory_mb": 2048, "network": "lan-a"}))
    files.append({"nom": "vm-config.json", "role": "config"})
    for f in files:
        f["taille"] = (d / f["nom"]).stat().st_size
        f["sha256"] = backup_integrity.sha256_of(d / f["nom"])
    if corrupt:
        (d / f"{disks[0]}.qcow2").write_bytes(b"changed after the backup")
    manifest = {
        "version": 1,
        "vm": vm,
        "source": source,
        "mode": "froid",
        "cree_le": f"2026-10-0{stamp[-1]}T00:00:00+00:00",
        "firmware": "uefi",
        "fichiers": files,
    }
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d


class FakeConn:
    def __init__(self, domains=(), networks=("default",)):
        self.domains = list(domains)
        self.networks = set(networks)
        self.defined = []

    def listAllDomains(self):
        return [type("D", (), {"name": lambda self, n=n: n})() for n in self.domains]

    def networkLookupByName(self, name):
        if name not in self.networks:
            raise libvirt.libvirtError("no network")
        return object()

    def listAllStoragePools(self):
        return []

    def close(self):
        pass


@pytest.fixture(autouse=True)
def allowed(tmp_path, monkeypatch):
    """The scenarios write their backups under tmp_path: it stands for a storage this node knows (the real list of
    allowed directories has its own test)."""
    monkeypatch.setattr(site_recovery, "_allowed_roots", lambda: [os.path.realpath(tmp_path)])


@pytest.fixture()
def conn(monkeypatch):
    c = FakeConn(domains=["db"])
    monkeypatch.setattr("app.core.libvirt_utils.open_conn", lambda *a, **k: c)
    return c


def test_the_scan_lists_the_other_sites_backups_newest_first(tmp_path, database, conn):
    share = tmp_path / "share"
    write_backup(share, "web", "20261001T0000001")
    write_backup(share, "web", "20261003T0000003")
    write_backup(share, "db", "20261002T0000002", disks=("vda", "vdb"))
    (share / "not a vm").mkdir()  # an invalid VM name is ignored
    (share / "empty").mkdir()  # a VM directory without a backup is not listed
    (share / "web" / "20261004T0000004").mkdir()  # no manifest: an unfinished copy

    found = {v["vm"]: v for v in site_recovery.scan(str(share))}
    assert sorted(found) == ["db", "web"]
    assert [b["cree_le"][:10] for b in found["web"]["sauvegardes"]] == ["2026-10-03", "2026-10-01"]
    assert found["web"]["sauvegardes"][0]["source"] == "site-a"
    assert found["db"]["sauvegardes"][0]["disques"] == 2
    assert found["db"]["existe_ici"] is True and found["web"]["existe_ici"] is False
    assert found["web"]["sauvegardes"][0]["backup_id"] is None


def test_links_planted_on_the_share_are_not_followed(tmp_path, database, conn):
    share = tmp_path / "share"
    elsewhere = tmp_path / "elsewhere"
    write_backup(elsewhere, "secret", "20261001T0000001")
    share.mkdir()
    os.symlink(elsewhere / "secret", share / "secret")
    assert site_recovery.scan(str(share)) == []
    with pytest.raises(site_recovery.RecoveryError):
        site_recovery.register(str(share / "secret" / "20261001T0000001"))


def test_the_scan_refuses_a_directory_reached_through_a_link(tmp_path, database, conn):
    # A link would lead past the check of system directories: /srv/share -> /etc, say.
    os.symlink("/etc", tmp_path / "share")
    with pytest.raises(site_recovery.RecoveryError, match="symbolic link"):
        site_recovery.scan(str(tmp_path / "share"))


def test_only_storage_this_node_knows_can_be_scanned(tmp_path, database, monkeypatch):
    # The real list: mount points, pools, the backup directory and the jobs' directories; anything else is refused.
    monkeypatch.setattr(site_recovery, "_allowed_roots", REAL_ALLOWED_ROOTS)
    pool = tmp_path / "pool"
    pool.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "pool-sibling").mkdir()  # starts with the pool's path, but is not under it

    class Pool:
        def XMLDesc(self, flags):
            return f"<pool type='netfs'><target><path>{pool}</path></target></pool>"

    class Conn(FakeConn):
        def listAllStoragePools(self):
            return [Pool()]

    monkeypatch.setattr("app.core.libvirt_utils.open_conn", lambda *a, **k: Conn())
    roots = site_recovery._allowed_roots()
    assert os.path.realpath(pool) in roots and "/mnt" in roots and str(backups.DEFAULT_BACKUP_DIR) in roots
    assert site_recovery.scan(str(pool)) == []
    with pytest.raises(site_recovery.RecoveryError, match="outside the storage this node knows"):
        site_recovery.scan(str(tmp_path / "elsewhere"))
    with pytest.raises(site_recovery.RecoveryError, match="outside the storage this node knows"):
        site_recovery.scan(str(tmp_path / "pool-sibling"))


@pytest.mark.parametrize("bad", ["relative/path", "/etc", "/var/../etc", "/nonexistent-dir"])
def test_the_scan_refuses_system_and_invalid_directories(bad, database, conn):
    with pytest.raises(site_recovery.RecoveryError):
        site_recovery.scan(bad)


def test_registering_adds_the_backup_once_with_its_origin(tmp_path, database):
    d = write_backup(tmp_path / "share", "web", "20261001T0000001")
    first = site_recovery.register(str(d))
    assert site_recovery.register(str(d)) == first
    row = backups._store().get(first)
    assert row["statut"] == "termine" and row["importe_de"] == "site-a" and row["vm_name"] == "web"
    assert row["checksum_sha256"] == backup_integrity.sha256_of(d / "vda.qcow2")


def test_the_plan_refuses_names_in_use_twice_or_invalid_and_a_missing_network(tmp_path, database, conn):
    d = str(write_backup(tmp_path / "share", "web", "20261001T0000001"))
    with pytest.raises(site_recovery.RecoveryError, match="already"):
        site_recovery.plan([{"chemin": d, "nom": "db"}], None)
    with pytest.raises(site_recovery.RecoveryError, match="twice"):
        site_recovery.plan([{"chemin": d, "nom": "web2"}, {"chemin": d, "nom": "web2"}], None)
    with pytest.raises(site_recovery.RecoveryError):
        site_recovery.plan([{"chemin": d, "nom": "bad name"}], None)
    with pytest.raises(site_recovery.RecoveryError, match="network"):
        site_recovery.plan([{"chemin": d, "nom": "web"}], "lan-b")
    with pytest.raises(site_recovery.RecoveryError, match="No Hyperlite backup"):
        site_recovery.plan([{"chemin": str(tmp_path), "nom": "web"}], None)
    assert site_recovery.plan([{"chemin": d, "nom": "web"}], "default") == [(d, "web")]


def test_a_recovery_restores_each_vm_and_reports_the_ones_that_failed(tmp_path, database, conn, monkeypatch):
    share = tmp_path / "share"
    good = write_backup(share, "web", "20261001T0000001")
    bad = write_backup(share, "app", "20261001T0000001", corrupt=True)
    calls, finished = [], {}

    def fake_restore(backup_id, mode, new_name=None, username=None, network=None, claim=None):
        row = backups._store().get(backup_id)
        status, _problems, _ = backup_integrity.verify(row["chemin"], row["checksum_sha256"])
        if status != backup_integrity.VERIFIED:
            raise RuntimeError("The backup failed its integrity check")
        calls.append((mode, new_name, network))

    class Inline:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("app.core.backups.restore_backup", fake_restore)
    monkeypatch.setattr(site_recovery.threading, "Thread", Inline)
    monkeypatch.setattr(site_recovery, "update_task_progress", lambda *a: None)
    monkeypatch.setattr(site_recovery, "finish_task", lambda task, status, msg=None: finished.update(s=status, m=msg))
    monkeypatch.setattr(backup_integrity, "_qemu_img_check", lambda path: None)

    site_recovery.recover([{"chemin": str(good), "nom": "web"}, {"chemin": str(bad), "nom": "app"}], "default", "admin")
    assert calls == [("new", "web", "default")]
    assert finished["s"] == "echec" and "1/2 VM(s) restored" in finished["m"] and "app:" in finished["m"]


def test_the_endpoints_are_for_administrators(client, auth_headers, tmp_path, conn):
    write_backup(tmp_path / "share", "web", "20261001T0000001")
    admin = auth_headers("admin1")
    r = client.get("/backups/site-recovery/scan", params={"chemin": str(tmp_path / "share")}, headers=admin)
    assert r.status_code == 200 and r.json()[0]["vm"] == "web"
    assert client.get("/backups/site-recovery/scan", params={"chemin": "/etc"}, headers=admin).status_code == 422
    watcher = auth_headers("watcher", role="observateur")
    assert (
        client.get("/backups/site-recovery/scan", params={"chemin": str(tmp_path)}, headers=watcher).status_code == 403
    )
    assert client.post("/backups/site-recovery", json={"elements": []}, headers=admin).status_code == 422
