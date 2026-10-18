"""Updating a node without network from an update file (docs/design/updates-1.0.md step 6): the file is built by the
real scripts/build-update-file.sh and signed with a throwaway key; a node accepts it only signed by the key it trusts,
complete, unmodified and for its Debian release, then updates from its packages alone."""

import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from app.core import host_system, offline_update
from app.core.tasks import create_task
from app.routers import update

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (shutil.which("gpg") and shutil.which("gpgv")), reason="needs gpg and gpgv")


def _key(home):
    home.mkdir(mode=0o700)
    env = {"GNUPGHOME": str(home), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        ["gpg", "--batch", "--quiet", "--pinentry-mode", "loopback", "--passphrase", "",
         "--quick-generate-key", "Test <t@example.org>", "ed25519", "sign", "0"],
        check=True, env=env, capture_output=True,
    )  # fmt: skip
    return env


def _build(tmp, env, version="1.0.1", debian="13", notes=True):
    deb, bundle, notes_dir = tmp / "pkg.deb", tmp / "bundle", tmp / "notes"
    deb.write_bytes(b"a debian package")
    (bundle / "debs").mkdir(parents=True, exist_ok=True)
    (bundle / "debs" / "packages.index").write_text("Package: libvirt0\n")
    (bundle / "debs" / "libvirt0.deb").write_bytes(b"libvirt")
    (bundle / "bundle-info").write_text(f"debian={debian} (trixie)\n")
    notes_dir.mkdir(exist_ok=True)
    if notes:
        (notes_dir / f"{version}.en.md").write_text("What is new")
        (notes_dir / f"{version}.fr.md").write_text("Nouveautés")
    # Relative paths, run from tmp, as scripts/ci-publish.sh calls it from the repository.
    subprocess.run(
        ["bash", str(ROOT / "scripts/build-update-file.sh"), version, "pkg.deb", "bundle", "notes",
         "hyperlite-update-amd64.tar"],
        check=True, env=env, capture_output=True, cwd=tmp,
    )  # fmt: skip
    return tmp / "hyperlite-update-amd64.tar"


@pytest.fixture()
def node(tmp_path, monkeypatch):
    """A node trusting the throwaway key, running Debian 13, and the key's environment to build files with."""
    env = _key(tmp_path / "gnupg")
    keyring = tmp_path / "keyring.gpg"
    public = subprocess.run(["gpg", "--export"], check=True, env=env, capture_output=True).stdout
    keyring.write_bytes(public)
    monkeypatch.setattr(offline_update, "KEYRING", keyring)
    monkeypatch.setattr(offline_update, "WORK_DIR", tmp_path / "work")
    (tmp_path / "os-release").write_text('PRETTY_NAME="Debian GNU/Linux 13"\nVERSION_ID="13"\n')
    monkeypatch.setattr(offline_update, "OS_RELEASE", tmp_path / "os-release")
    return env, tmp_path


def _rewrite(src, dst, change):
    """A copy of the archive with change(name, data) -> data (None drops the member) applied to each member."""
    with tarfile.open(src) as tin, tarfile.open(dst, "w") as tout:
        for m in tin.getmembers():
            data = tin.extractfile(m).read() if m.isfile() else None
            if m.isfile():
                data = change(m.name, data)
                if data is None:
                    continue
                m.size = len(data)
            tout.addfile(m, io.BytesIO(data) if data is not None else None)
    return dst


def test_a_published_file_is_accepted_and_extracted(node):
    env, tmp = node
    with open(_build(tmp, env), "rb") as f:
        info = offline_update.receive(f)
    assert (info["version"], info["debian"], info["notes"]) == (
        "1.0.1",
        "13",
        {"en": "What is new", "fr": "Nouveautés"},
    )
    assert offline_update.stored()["version"] == "1.0.1"
    version, package, bundle = offline_update.extract()
    assert version == "1.0.1" and package.read_bytes() == b"a debian package"
    assert not (bundle / "wheels").exists()  # the hyperlite package carries its Python environment
    opts = offline_update.local_apt_options(bundle)
    assert (bundle / "debs" / "Packages").read_text() == "Package: libvirt0\n"
    assert (
        f"Dir::Etc::sourcelist={offline_update.WORK_DIR / 'local.list'}" in opts and "Dir::Etc::sourceparts=-" in opts
    )
    assert (offline_update.WORK_DIR / "local.list").read_text() == f"deb [trusted=yes] file:{bundle / 'debs'} ./\n"
    offline_update.discard()
    assert offline_update.stored() is None


def test_a_file_signed_by_another_key_is_refused(node):
    _env, tmp = node
    other = _key(tmp / "other")
    with open(_build(tmp, other), "rb") as f, pytest.raises(offline_update.OfflineUpdateError, match="signature"):
        offline_update.receive(f)
    assert not offline_update.upload_path().exists()


def test_a_modified_package_is_refused_before_anything_is_installed(node):
    env, tmp = node
    bad = _rewrite(_build(tmp, env), tmp / "bad.tar", lambda n, d: b"evil" if n.endswith("libvirt0.deb") else d)
    with open(bad, "rb") as f:
        offline_update.receive(f)  # the manifest is genuine: the damage shows when the contents are checked
    with pytest.raises(offline_update.OfflineUpdateError, match="does not match"):
        offline_update.extract()
    assert not offline_update.extracted_dir().exists()


def test_an_entry_the_manifest_does_not_list_is_refused(node):
    env, tmp = node
    src = _build(tmp, env)
    bad = tmp / "extra.tar"
    shutil.copy(src, bad)
    with tarfile.open(bad, "a") as t:
        info = tarfile.TarInfo("offline/debs/backdoor.deb")
        info.size = 4
        t.addfile(info, io.BytesIO(b"evil"))
    with open(bad, "rb") as f, pytest.raises(offline_update.OfflineUpdateError, match="unexpected entry"):
        offline_update.receive(f)


def test_a_manifest_changed_after_signing_is_refused(node):
    env, tmp = node

    def change(name, data):
        if name != "manifest.json":
            return data
        m = json.loads(data)
        m["version"] = "9.9.9"
        return json.dumps(m).encode()

    bad = _rewrite(_build(tmp, env), tmp / "bad.tar", change)
    with open(bad, "rb") as f, pytest.raises(offline_update.OfflineUpdateError, match="signature"):
        offline_update.receive(f)


def test_a_file_for_another_debian_release_is_refused(node):
    env, tmp = node
    with (
        open(_build(tmp, env, debian="12"), "rb") as f,
        pytest.raises(offline_update.OfflineUpdateError, match="Debian 12"),
    ):
        offline_update.receive(f)


def test_anything_else_is_refused(node):
    for content in (b"", b"not a tar at all", b"\x1f\x8b compressed"):
        with pytest.raises(offline_update.OfflineUpdateError):
            offline_update.receive(io.BytesIO(content))


def test_the_api(node, client, auth_headers, monkeypatch):
    env, tmp = node
    monkeypatch.setattr(update, "_install_method", lambda: "apt")
    monkeypatch.setattr(update, "_dpkg_installed_version", lambda: "1.0.0")
    monkeypatch.setattr(update, "_newer", lambda a, b: tuple(map(int, a.split("."))) > tuple(map(int, b.split("."))))
    started = []
    monkeypatch.setattr(update, "_run_update_job_apt", lambda *a: started.append(a))
    admin = auth_headers("root")
    assert client.get("/update/offline", headers=admin).json() == {"version": None}
    with open(_build(tmp, env), "rb") as f:
        r = client.post("/update/offline?lang=fr", files={"file": ("u.tar", f)}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json() == {"version": "1.0.1", "debian": "13", "taille_mo": r.json()["taille_mo"], "installee": "1.0.0",
                        "plus_ancienne": False, "notes": "Nouveautés"}  # fmt: skip
    assert client.get("/update/offline", headers=admin).json()["version"] == "1.0.1"
    eve = auth_headers("eve", role="observateur")
    assert client.post("/update/offline/apply", json={}, headers=eve).status_code == 403
    r = client.post("/update/offline/apply", json={"systeme": True}, headers=admin)
    assert r.status_code == 202 and r.json()["version"] == "1.0.1"
    assert started[0][1:] == ("root", True, True)  # (task, user, system, offline)
    # A refused file leaves the node as it was, and says why.
    r = client.post("/update/offline", files={"file": ("u.tar", b"junk")}, headers=admin)
    assert r.status_code == 422 and "not a Hyperlite update file" in r.json()["detail"]
    assert client.delete("/update/offline", headers=admin).json() == {"version": None}
    assert client.post("/update/offline/apply", json={}, headers=admin).status_code == 409


def test_an_older_file_is_not_installed(node, client, auth_headers, monkeypatch):
    env, tmp = node
    monkeypatch.setattr(update, "_install_method", lambda: "apt")
    monkeypatch.setattr(update, "_dpkg_installed_version", lambda: "1.2.0")
    monkeypatch.setattr(update, "_newer", lambda a, b: tuple(map(int, a.split("."))) > tuple(map(int, b.split("."))))
    with open(_build(tmp, env), "rb") as f:
        offline_update.receive(f)
    r = client.post("/update/offline/apply", json={}, headers=auth_headers("root"))
    assert r.status_code == 409 and "older" in r.json()["detail"]


def test_the_update_uses_the_file_packages_only(node, database, monkeypatch):
    env, tmp = node
    with open(_build(tmp, env), "rb") as f:
        offline_update.receive(f)
    commands, spawned, apt_runs = [], [], []
    versions = iter(["1.0.0", "1.0.1"])
    monkeypatch.setattr(update, "_dpkg_installed_version", lambda: next(versions))
    monkeypatch.setattr(update.version, "STARTUP_VERSION", "1.0.0")
    monkeypatch.setattr(update, "_backup", lambda task_id: tmp / "backup.tar.gz")
    monkeypatch.setattr(update, "_keep_previous", lambda task_id: None)
    monkeypatch.setattr(update, "_apt_update_with_retry", lambda **k: pytest.fail("the network is not used"))
    monkeypatch.setattr(
        update, "_run_c", lambda cmd, **k: apt_runs.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")
    )
    monkeypatch.setattr(update, "_spawn_outside_service", lambda unit, argv: spawned.append(unit))
    monkeypatch.setattr(host_system, "REBOOT_REQUIRED", tmp / "reboot-required")
    monkeypatch.setattr(host_system, "run_upgrade", lambda cmd, log: commands.append(cmd) or 0)
    task_id = create_task("hyperlite_update", "hyperlite", username="alice")
    update._run_update_job_apt(task_id, "alice", True, True)
    with database.get_conn() as conn:
        assert conn.execute("SELECT statut FROM tasks WHERE id = ?", (task_id,)).fetchone()[0] == "termine"
    local = f"Dir::Etc::sourcelist={offline_update.WORK_DIR / 'local.list'}"
    assert local in apt_runs[0] and apt_runs[0][-1] == "update"
    full, install = commands
    assert local in full and "full-upgrade" in full
    assert local in install and install[-1].endswith("hyperlite_1.0.1_amd64.deb")
    for cmd in commands:  # the package's postinst runs no pip: nothing to point at the file's wheels
        assert not any(c.startswith("PIP_") for c in cmd)
    assert spawned == ["hyperlite-update-watchdog", "hyperlite-update-restart"]
    assert not offline_update.WORK_DIR.exists()  # applied: removed
