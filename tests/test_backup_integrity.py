"""Backup integrity: the manifest, verification (every checksum, qemu-img check of the images), the weekly
verification, and the NVRAM and TPM state of UEFI VMs saved and put back. Checked on a real libvirt 10 as well: a
Secure Boot VM with a TPM was backed up, verified, corrupted on purpose, and restored as a new VM that started with
the same NVRAM and TPM state (owned by swtpm)."""

import io
import json
import tarfile

import pytest

from app.core import backup_integrity as bi
from app.core import backups

UEFI_XML = """<domain><name>win</name><uuid>{uuid}</uuid><os firmware='efi'><type machine='q35'>hvm</type>
<nvram>{nvram}</nvram></os><devices><tpm model='tpm-crb'><backend type='emulator' version='2.0'/></tpm>
</devices></domain>"""
BIOS_XML = "<domain><name>lin</name><uuid>u-bios</uuid><os><type machine='pc'>hvm</type></os><devices/></domain>"


class Domain:
    def __init__(self, xml, uuid):
        self.xml = xml
        self.uuid = uuid

    def XMLDesc(self, flags=0):
        return self.xml

    def UUIDString(self):
        return self.uuid


@pytest.fixture()
def fw_dirs(tmp_path, monkeypatch):
    nvram_dir, swtpm = tmp_path / "nvram", tmp_path / "swtpm"
    nvram_dir.mkdir()
    swtpm.mkdir()
    monkeypatch.setattr(bi, "NVRAM_DIR", nvram_dir)
    monkeypatch.setattr(bi, "SWTPM_DIR", swtpm)
    monkeypatch.setattr(bi.shutil, "chown", lambda *a, **k: None)
    return nvram_dir, swtpm


def uefi_domain(nvram_dir, uuid="u-1"):
    return Domain(UEFI_XML.format(uuid=uuid, nvram=nvram_dir / "win_VARS.fd"), uuid)


@pytest.fixture(autouse=True)
def images_are_consistent(monkeypatch):
    monkeypatch.setattr(bi, "_qemu_img_check", lambda path: None)


def make_backup(path, with_firmware=False):
    path.mkdir(parents=True)
    (path / "sda.qcow2").write_bytes(b"disk-a" * 1000)
    (path / "sdb.qcow2").write_bytes(b"disk-b" * 1000)
    (path / "vm-config.json").write_text("{}")
    if with_firmware:
        (path / "nvram.fd").write_bytes(b"vars")
    return bi.write_manifest(
        path,
        "vm1",
        "froid",
        "uefi_secure" if with_firmware else "bios",
        [("sda", path / "sda.qcow2"), ("sdb", path / "sdb.qcow2")],
    )


def test_the_manifest_lists_every_file_with_its_role_size_and_checksum(tmp_path):
    manifest = make_backup(tmp_path / "b", with_firmware=True)
    roles = {f["nom"]: (f["role"], f.get("cible")) for f in manifest["fichiers"]}
    assert roles == {
        "sda.qcow2": ("disque", "sda"),
        "sdb.qcow2": ("disque", "sdb"),
        "nvram.fd": ("nvram", None),
        "vm-config.json": ("config", None),
    }
    assert all(len(f["sha256"]) == 64 and f["taille"] > 0 for f in manifest["fichiers"])
    assert json.loads((tmp_path / "b" / "manifest.json").read_text())["firmware"] == "uefi_secure"


def test_an_intact_backup_is_verified(tmp_path):
    make_backup(tmp_path / "b")
    assert bi.verify(tmp_path / "b") == (bi.VERIFIED, [], 3)


@pytest.mark.parametrize(
    ("damage", "text"),
    [
        (lambda d: (d / "sdb.qcow2").unlink(), "sdb.qcow2: missing"),
        (lambda d: (d / "sda.qcow2").write_bytes(b"x"), "size differs"),
        (lambda d: (d / "sda.qcow2").write_bytes(b"disk-A" * 1000), "checksum differs"),
        (lambda d: (d / "manifest.json").write_text('{"fichiers": []}'), "lists no disk"),
    ],
)
def test_a_damaged_backup_is_corrupt_with_the_reason(tmp_path, damage, text):
    make_backup(tmp_path / "b")
    damage(tmp_path / "b")
    status, problems, _ = bi.verify(tmp_path / "b")
    assert status == bi.CORRUPT and any(text in p for p in problems)


def test_an_image_qemu_img_rejects_is_corrupt(tmp_path, monkeypatch):
    make_backup(tmp_path / "b")
    monkeypatch.setattr(bi, "_qemu_img_check", lambda path: "Leaked cluster 12" if path.name == "sdb.qcow2" else None)
    status, problems, _ = bi.verify(tmp_path / "b")
    assert status == bi.CORRUPT and problems == ["sdb.qcow2: Leaked cluster 12"]


def test_a_manifest_cannot_point_outside_its_backup(tmp_path):
    make_backup(tmp_path / "b")
    (tmp_path / "secret").write_text("x")
    manifest = json.loads((tmp_path / "b" / "manifest.json").read_text())
    manifest["fichiers"].append({"nom": "../secret", "role": "config", "taille": 1, "sha256": "0"})
    (tmp_path / "b" / "manifest.json").write_text(json.dumps(manifest))
    _status, problems, _ = bi.verify(tmp_path / "b")
    assert "secret: missing" in problems  # read as a name inside the backup, never followed


def test_a_backup_from_before_manifests_is_checked_with_what_it_has(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "sda.qcow2").write_bytes(b"old-disk")
    good = bi.sha256_of(old / "sda.qcow2")
    assert bi.verify(old, single_checksum=good)[0] == bi.VERIFIED
    status, problems, _ = bi.verify(old, single_checksum="0" * 64)
    assert status == bi.CORRUPT and "checksum differs" in problems[0]
    assert bi.verify(tmp_path / "gone")[0] == bi.CORRUPT


def test_nvram_and_tpm_state_are_saved_and_put_back_for_a_new_vm(tmp_path, fw_dirs):
    nvram_dir, swtpm = fw_dirs
    (nvram_dir / "win_VARS.fd").write_bytes(b"boot-entries")
    state = swtpm / "u-1" / "tpm2"
    state.mkdir(parents=True)
    (state / "tpm2-00.permall").write_bytes(b"sealed-keys")
    (state / ".lock").write_text("")
    dest = tmp_path / "backup"
    dest.mkdir()
    assert bi.save_firmware_state(uefi_domain(nvram_dir), dest) == ["nvram.fd", "tpm.tar"]
    with tarfile.open(dest / "tpm.tar") as tar:
        assert sorted(tar.getnames()) == ["tpm2", "tpm2/tpm2-00.permall"]  # swtpm's lock is left out

    restored = Domain(UEFI_XML.format(uuid="u-2", nvram=nvram_dir / "win2_VARS.fd"), "u-2")
    assert bi.restore_firmware_state(dest, restored) == ["nvram", "tpm"]
    assert (nvram_dir / "win2_VARS.fd").read_bytes() == b"boot-entries"
    assert (swtpm / "u-2" / "tpm2" / "tpm2-00.permall").read_bytes() == b"sealed-keys"
    assert oct((nvram_dir / "win2_VARS.fd").stat().st_mode & 0o777) == "0o600"


def test_a_bios_vm_has_no_firmware_state(tmp_path, fw_dirs):
    dest = tmp_path / "backup"
    dest.mkdir()
    assert bi.save_firmware_state(Domain(BIOS_XML, "u-bios"), dest) == []
    assert bi.restore_firmware_state(dest, Domain(BIOS_XML, "u-bios")) == []


def test_a_tpm_archive_cannot_write_outside_the_vm_state(tmp_path, fw_dirs):
    nvram_dir, swtpm = fw_dirs
    dest = tmp_path / "backup"
    dest.mkdir()
    with tarfile.open(dest / "tpm.tar", "w") as tar:
        info = tarfile.TarInfo("../../escaped")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(tarfile.FilterError):
        bi.restore_firmware_state(dest, uefi_domain(nvram_dir))
    assert not (tmp_path / "escaped").exists() and not (swtpm.parent / "escaped").exists()


# --- Recorded results, the endpoint and the weekly run ---------------------------------------------------------


def _insert(database, path, verifie_le=None, statut="termine", cree_le="2026-01-01T00:00:00+00:00"):
    with database.get_conn() as db:
        cur = db.execute(
            "INSERT INTO backups (vm_name, chemin, mode, cree_le, statut, verifie_le) VALUES ('vm1', ?, 'froid', ?, ?, ?)",
            (str(path), cree_le, statut, verifie_le),
        )
        db.commit()
        return cur.lastrowid


def _row(database, backup_id):
    with database.get_conn() as db:
        return dict(db.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone())


def test_a_corrupt_backup_is_recorded_audited_and_notified(database, tmp_path, monkeypatch):
    make_backup(tmp_path / "b")
    (tmp_path / "b" / "sda.qcow2").write_bytes(b"x")
    sent = []
    monkeypatch.setattr("app.core.notifications.notify", lambda *a, **k: sent.append(a))
    backup_id = _insert(database, tmp_path / "b")
    result = backups.verify_backup(backup_id, username="admin")
    assert result["verification"] == "corrompu"
    row = _row(database, backup_id)
    assert row["verification"] == "corrompu" and "sda.qcow2" in row["verification_detail"] and row["verifie_le"]
    assert sent and sent[0][0] == "verify_backup"


def test_the_endpoint_verifies_as_a_task_admins_only(client, auth_headers, database, tmp_path, monkeypatch):
    make_backup(tmp_path / "b")
    admin = auth_headers("admin1")
    backup_id = _insert(database, tmp_path / "b")
    import app.routers.backups as router

    real_thread = router.threading.Thread

    class Inline:  # runs the endpoint's job at once; any other thread (the audit writer...) stays a real one
        def __init__(self, target=None, args=(), daemon=None, **kw):
            self.target, self.args, self.real = target, args, None
            if getattr(target, "__name__", "") != "job":
                self.real = real_thread(target=target, args=args, daemon=daemon, **kw)

        def start(self):
            if self.real is not None:
                self.real.start()
            else:
                self.target(*self.args)

    monkeypatch.setattr(router.threading, "Thread", Inline)
    r = client.post(f"/backups/{backup_id}/verify", headers=admin)
    assert r.status_code == 202 and r.json()["task_id"]
    assert _row(database, backup_id)["verification"] == "verifie"
    with database.get_conn() as db:
        assert db.execute("SELECT statut FROM tasks WHERE id = ?", (r.json()["task_id"],)).fetchone()[0] == "termine"
    watcher = auth_headers("watcher", role="observateur")
    assert client.post(f"/backups/{backup_id}/verify", headers=watcher).status_code == 403
    assert client.post("/backups/999/verify", headers=admin).status_code == 404
    running = _insert(database, tmp_path / "b", statut="en_cours")
    assert client.post(f"/backups/{running}/verify", headers=admin).status_code == 409


def test_the_weekly_run_takes_the_oldest_unverified_one_and_waits_for_running_backups(database, tmp_path):
    for name in ("recent", "never", "stale"):
        make_backup(tmp_path / name)
    recent = _insert(database, tmp_path / "recent", verifie_le=backups._now().isoformat())
    never = _insert(database, tmp_path / "never")
    stale = _insert(database, tmp_path / "stale", verifie_le="2020-01-01T00:00:00+00:00")
    assert backups._verify_due_backup()["verification"] == "verifie"
    assert _row(database, never)["verifie_le"] and _row(database, stale)["verifie_le"].startswith("2020")
    backups._verify_due_backup()
    assert not _row(database, stale)["verifie_le"].startswith("2020")
    assert backups._verify_due_backup() is None  # everything verified this week
    assert _row(database, recent)["verification"] is None

    with database.get_conn() as db:
        db.execute("UPDATE backups SET verifie_le = NULL WHERE id = ?", (recent,))
        db.commit()
    with backups._backup_lock:
        assert backups._verify_due_backup() is None
    assert _row(database, recent)["verifie_le"] is None
