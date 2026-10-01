"""Replication to another site: job validation, the scheduler, the per-VM status and its late flag, chain-aware
verification and pruning, and the admin-only API. The copies themselves run on a real QEMU VM in
test_replication_qemu.py."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core import backup_integrity, replication


def job_payload(**over):
    return {"nom": "to site B", "selection": "toutes", "cible_dir": "/mnt/site-b", "intervalle_minutes": 15, **over}


@pytest.fixture()
def vms(monkeypatch):
    names = ["web", "db"]
    monkeypatch.setattr("app.core.backup_groups._local_vm_names", lambda: sorted(names))
    return names


@pytest.mark.parametrize(
    ("over", "text"),
    [
        ({"cible_dir": "/etc/x"}, "cannot be written"),
        ({"cible_dir": ""}, "directory"),
        ({"intervalle_minutes": 2}, "5 to 1440"),
        ({"intervalle_minutes": 2000}, "5 to 1440"),
        ({"nom": "bad/name"}, "Invalid name"),
        ({"selection": "x"}, "selection"),
        ({"selection": "pool", "valeur": "999"}, "pool"),
    ],
)
def test_invalid_jobs_are_refused(database, vms, over, text):
    with pytest.raises(replication.ReplicationError, match=text):
        replication.save_job(job_payload(**over))


def test_a_job_lists_its_vms_and_names_are_unique(database, vms):
    job = replication.save_job(job_payload(exclues=["db"]))
    assert job["vms"] == ["web"] and job["intervalle_minutes"] == 15 and job["en_cours"] is False
    with pytest.raises(replication.ReplicationError, match="already named"):
        replication.save_job(job_payload())
    assert replication.save_job(job_payload(nom="other"), job_id=9999) is None


def test_due_jobs_start_in_the_background_and_move_their_next_run(database, vms, monkeypatch):
    started = []
    monkeypatch.setattr(replication, "start_job", lambda job, user: started.append(job["nom"]))
    job = replication.save_job(job_payload())
    now = datetime.now(UTC) + timedelta(seconds=1)
    replication.run_due(now)
    assert started == ["to site B"]
    stored = replication._store().get_job(job["id"])
    assert stored["prochaine_execution"] == (now + timedelta(minutes=15)).isoformat()
    replication.run_due(now)  # not due again before its interval
    assert started == ["to site B"]


def test_run_job_copies_each_vm_and_reports_failures_without_stopping(database, vms, monkeypatch):
    def fake(vm, target, username):
        if vm == "db":
            raise replication.ReplicationError("Disk vda is not qcow2")
        return "incremental"

    monkeypatch.setattr(replication, "replicate", fake)
    job = replication.save_job(job_payload())
    assert replication.run_job(job) == {"db": "failed: Disk vda is not qcow2", "web": "incremental"}


def test_the_status_flags_a_vm_without_a_recent_copy(database, vms):
    replication.save_job(job_payload())
    now = datetime.now(UTC)
    store = replication._store()
    store.save_state(
        "web", cible_dir="/mnt/site-b", statut="ok", dernier_ok_le=(now - timedelta(minutes=10)).isoformat()
    )
    store.save_state(
        "db", cible_dir="/mnt/site-b", statut="ok", dernier_ok_le=(now - timedelta(minutes=45)).isoformat()
    )
    rows = {r["vm_name"]: r for r in replication.status(now)}
    assert rows["web"]["en_retard"] is False and rows["web"]["intervalle_minutes"] == 15
    assert rows["db"]["en_retard"] is True and rows["db"]["age_s"] == 45 * 60


def point(root, vm, stamp, kind, chain, parent=None, extra=None):
    d = root / vm / stamp
    d.mkdir(parents=True)
    (d / "vda.qcow2").write_bytes(b"x")
    files = [
        {
            "nom": "vda.qcow2",
            "role": "disque",
            "cible": "vda",
            "taille": 1,
            "sha256": backup_integrity.sha256_of(d / "vda.qcow2"),
        }
    ]
    manifest = {"version": 1, "vm": vm, "mode": "chaud", "fichiers": files}
    if kind:
        manifest["replication"] = {"type": kind, "chaine": chain, "parent": parent}
    manifest.update(extra or {})
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d


def test_pruning_keeps_the_newest_chains_and_never_an_ordinary_backup(tmp_path):
    for chain in ("20261001T000000Z", "20261002T000000Z", "20261003T000000Z"):
        point(tmp_path, "web", chain, "complet", chain)
        point(tmp_path, "web", chain[:-2] + "1Z", "incremental", chain, parent=chain)
    point(tmp_path, "web", "20260101T000000Z", None, None)  # a backup, not a replication point
    replication._prune(tmp_path / "web", "20261003T000000Z")
    left = sorted(p.name for p in (tmp_path / "web").iterdir())
    assert left == ["20260101T000000Z", "20261002T000000Z", "20261002T000001Z", "20261003T000000Z", "20261003T000001Z"]


def test_verification_follows_the_chain_and_refuses_a_parent_outside_it(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_integrity, "_qemu_img_check", lambda path: None)
    full = point(tmp_path, "web", "a", "complet", "a")
    inc = point(tmp_path, "web", "b", "incremental", "a", parent="a")
    assert backup_integrity.verify(inc)[0] == backup_integrity.VERIFIED
    (full / "vda.qcow2").write_bytes(b"y")  # the base changed: every point after it is corrupt too
    status, problems, _ = backup_integrity.verify(inc)
    assert status == backup_integrity.CORRUPT and any(p.startswith("a: ") for p in problems)
    evil = point(tmp_path, "web", "c", "incremental", "a", parent="../../etc")
    assert backup_integrity.verify(evil)[0] == backup_integrity.CORRUPT


def test_a_vm_whose_disks_cannot_be_replicated_says_why(monkeypatch):
    class Dom:
        def __init__(self, xml):
            self.xml = xml

        def XMLDesc(self, flags=0):
            return self.xml

    block = "<domain><devices><disk type='block' device='disk'><source dev='/dev/zvol/t/v'/><target dev='vda'/></disk></devices></domain>"
    raw = "<domain><devices><disk type='file' device='disk'><driver type='raw'/><source file='/i/a.img'/><target dev='vda'/></disk></devices></domain>"
    qcow = "<domain><devices><disk type='file' device='disk'><driver type='qcow2'/><source file='/i/a.qcow2'/><target dev='vda'/></disk></devices></domain>"
    with pytest.raises(replication.ReplicationError, match="not a file"):
        replication.disks_of(Dom(block))
    with pytest.raises(replication.ReplicationError, match="not qcow2"):
        replication.disks_of(Dom(raw))
    monkeypatch.setattr(replication, "_qcow2_compat", lambda path: "0.10")
    with pytest.raises(replication.ReplicationError, match=r"compat=1\.1"):
        replication.disks_of(Dom(qcow))
    monkeypatch.setattr(replication, "_qcow2_compat", lambda path: "1.1")
    assert replication.disks_of(Dom(qcow)) == [("vda", "/i/a.qcow2")]


def test_the_api_is_for_administrators(client, auth_headers, vms, monkeypatch):
    started = []
    monkeypatch.setattr(replication, "start_job", lambda job, user: started.append(job["id"]))
    admin = auth_headers("admin1")
    r = client.post("/replication/jobs", json=job_payload(), headers=admin)
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]
    assert (
        client.post("/replication/jobs", json=job_payload(nom="x", cible_dir="/etc"), headers=admin).status_code == 422
    )
    assert (
        client.put(f"/replication/jobs/{job_id}", json=job_payload(intervalle_minutes=60), headers=admin).json()[
            "intervalle_minutes"
        ]
        == 60
    )
    assert client.post(f"/replication/jobs/{job_id}/run", headers=admin).status_code == 202 and started == [job_id]
    assert client.get("/replication/status", headers=admin).status_code == 200
    watcher = auth_headers("watcher", role="observateur")
    assert client.get("/replication/jobs", headers=watcher).status_code == 403
    assert client.get("/replication/status", headers=watcher).status_code == 403
    assert client.delete(f"/replication/jobs/{job_id}", headers=admin).status_code == 200
    assert client.delete(f"/replication/jobs/{job_id}", headers=admin).status_code == 404
