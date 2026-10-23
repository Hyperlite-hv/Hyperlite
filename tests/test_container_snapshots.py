"""Container snapshots (app/core/container_snapshots.py): copies of a stopped container's directory, each linking the
files the previous one holds unchanged, restored by a copy swapped in. These run rsync on real directories; the
container file system a node holds (devices, ACLs, capabilities, other owners, 1.1 GB) was checked as root in
docs/design/container-snapshots.md."""

import contextlib
import json
import os
import shutil
import time
import xml.etree.ElementTree as ET
from collections import namedtuple
from pathlib import Path

import libvirt
import pytest

from app.core import container_builder, container_snapshots
from app.routers import containers

pytestmark = pytest.mark.skipif(shutil.which("rsync") is None, reason="needs rsync")


@pytest.fixture()
def place(tmp_path, monkeypatch):
    """A containers directory of its own, with a container "web" in it."""
    monkeypatch.setattr(container_builder, "CONTAINERS_DIR", tmp_path)
    monkeypatch.setattr(container_builder, "container_rootfs_path", lambda name: tmp_path / name)
    monkeypatch.setattr(container_snapshots, "container_rootfs_path", lambda name: tmp_path / name)
    root = tmp_path / "web"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "hostname").write_text("web\n")
    (root / "etc" / "motd").write_text("version 1\n")
    (root / "etc" / "secret").write_text("s\n")
    (root / "etc" / "secret").chmod(0o600)
    (root / "etc" / "link").symlink_to("hostname")
    os.link(root / "etc" / "hostname", root / "etc" / "hostname-hard")
    with contextlib.suppress(OSError):  # a file system without user extended attributes: the rest still applies
        os.setxattr(root / "etc" / "hostname", "user.hyperlite", b"x")
    return tmp_path


def _tree(root):
    """What a restore must give back: per path, type and mode, size, content, link target, xattrs."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for entry in dirnames + filenames:
            p = os.path.join(dirpath, entry)
            st = os.lstat(p)
            item = {"mode": st.st_mode, "nlink": st.st_nlink if not os.path.isdir(p) else None}
            if os.path.islink(p):
                item["target"] = os.readlink(p)
            elif os.path.isfile(p):
                item["content"] = Path(p).read_bytes()
                item["mtime"] = st.st_mtime_ns
            item["xattr"] = {
                k: os.getxattr(p, k, follow_symlinks=False) for k in os.listxattr(p, follow_symlinks=False)
            }
            out[os.path.relpath(p, root)] = item
    return out


def test_a_snapshot_is_restored_exactly(place):
    root = place / "web"
    before = _tree(root)
    meta = container_snapshots.create("web", "clean", "before the upgrade")
    assert meta["name"] == "clean" and meta["description"] == "before the upgrade"
    (root / "etc" / "motd").write_text("version 2, longer\n")
    (root / "etc" / "secret").unlink()
    (root / "etc" / "new").write_text("added\n")

    container_snapshots.restore("web", "clean")
    assert _tree(root) == before
    assert sorted(os.listdir(place)) == [".snapshots", "web"], "nothing left next to the container"
    snap_file = place / ".snapshots" / "web" / "clean" / "rootfs" / "etc" / "motd"
    assert os.stat(root / "etc" / "motd").st_ino != os.stat(snap_file).st_ino, (
        "a copy: the container never writes into its snapshot"
    )
    assert oct((place / ".snapshots").stat().st_mode & 0o777) == "0o700"


def test_a_file_changed_without_its_size_or_mtime_is_in_the_next_snapshot(place):
    """rsync's quick check (size and mtime) linked the previous snapshot's file: the snapshot kept the old content."""
    motd = place / "web" / "etc" / "motd"
    container_snapshots.create("web", "one")
    st = motd.stat()
    motd.write_text("version 2\n")  # the same size
    os.utime(motd, ns=(st.st_atime_ns, st.st_mtime_ns))
    container_snapshots.create("web", "two")
    assert (place / ".snapshots" / "web" / "two" / "rootfs" / "etc" / "motd").read_text() == "version 2\n"


def test_unchanged_files_are_shared_with_the_previous_snapshot(place):
    container_snapshots.create("web", "one")
    (place / "web" / "etc" / "motd").write_text("version 2, longer\n")
    container_snapshots.create("web", "two")
    one, two = (place / ".snapshots" / "web" / s / "rootfs" / "etc" for s in ("one", "two"))
    assert os.stat(one / "secret").st_ino == os.stat(two / "secret").st_ino
    assert os.stat(one / "motd").st_ino != os.stat(two / "motd").st_ino

    container_snapshots.delete("web", "one")
    assert (two / "secret").read_text() == "s\n", "deleting one snapshot leaves the next one whole"
    assert [s["name"] for s in container_snapshots.list_snapshots("web")] == ["two"]


def test_a_failed_snapshot_leaves_nothing_and_a_failed_restore_leaves_the_container(place, monkeypatch):
    container_snapshots.create("web", "one")
    before = _tree(place / "web")

    def broken(*args):
        raise container_snapshots.SnapshotError("rsync failed (11): No space left on device")

    monkeypatch.setattr(container_snapshots, "_rsync", broken)
    with pytest.raises(container_snapshots.SnapshotError):
        container_snapshots.create("web", "two")
    assert sorted(os.listdir(place / ".snapshots" / "web")) == ["one"]
    with pytest.raises(container_snapshots.SnapshotError):
        container_snapshots.restore("web", "one")
    assert _tree(place / "web") == before and sorted(os.listdir(place)) == [".snapshots", "web"]


def test_a_copy_cut_short_is_never_a_snapshot(place):
    """A service stopped in the middle of a snapshot leaves `<name>.partial`, its description already written."""
    partial = place / ".snapshots" / "web" / "two.partial"
    (partial / "rootfs").mkdir(parents=True)
    (partial / "snapshot.json").write_text(json.dumps({"name": "two", "created_at": "2026-01-01T00:00:00+00:00"}))
    assert container_snapshots.list_snapshots("web") == []
    assert not container_snapshots.exists("web", "two")
    container_snapshots.create("web", "two")
    assert [s["name"] for s in container_snapshots.list_snapshots("web")] == ["two"]


def test_a_full_copy_needs_room_for_it(place, monkeypatch):
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(container_snapshots.shutil, "disk_usage", lambda p: Usage(10**12, 0, 1024))
    with pytest.raises(container_snapshots.SnapshotError, match="Not enough free space"):
        container_snapshots.create("web", "one")
    assert container_snapshots.list_snapshots("web") == []


def test_snapshots_follow_the_container(place):
    container_snapshots.create("web", "one")
    os.rename(place / "web", place / "www")
    container_snapshots.rename_all("web", "www", place)
    assert [s["name"] for s in container_snapshots.list_snapshots("www")] == ["one"]
    container_snapshots.delete_all("www")
    assert not (place / ".snapshots" / "www").exists()


# ---- The API ----


class Domain:
    def __init__(self, name, active=False):
        self._name, self.active, self.started = name, active, False

    def isActive(self):
        return self.active

    def create(self):
        self.started = True

    def XMLDesc(self, flags=0):
        return f"<domain type='lxc'><name>{self._name}</name></domain>"

    def name(self):
        return self._name


class Conn:
    def __init__(self, domains):
        self.domains = domains

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def close(self):
        pass


@pytest.fixture()
def api(place, monkeypatch):
    domains = {"web": Domain("web")}
    monkeypatch.setattr(containers, "open_lxc_conn", lambda: Conn(domains))
    monkeypatch.setattr(containers, "_summary", lambda domain: {"name": domain.name()})
    return domains


def _wait(client, headers, task_id):
    for _ in range(200):
        task = client.get(f"/tasks/{task_id}", headers=headers).json()
        if task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.05)
    raise AssertionError("the task did not end")


def test_the_api_takes_lists_restores_and_deletes(client, auth_headers, api, place):
    admin = auth_headers("root")
    r = client.post("/containers/web/snapshots", json={"name": "clean", "description": "fresh"}, headers=admin)
    assert r.status_code == 202, r.text
    assert _wait(client, admin, r.json()["task_id"])["status"] == "succeeded"
    listed = client.get("/containers/web/snapshots", headers=auth_headers("eve", role="viewer")).json()["items"]
    assert [(s["name"], s["description"]) for s in listed] == [("clean", "fresh")]

    (place / "web" / "etc" / "motd").write_text("changed\n")
    r = client.post("/containers/web/snapshots/clean/restore", headers=admin)
    assert _wait(client, admin, r.json()["task_id"])["status"] == "succeeded"
    assert (place / "web" / "etc" / "motd").read_text() == "version 1\n"

    assert client.delete("/containers/web/snapshots/clean", headers=admin).status_code == 200
    assert client.get("/containers/web/snapshots", headers=admin).json()["items"] == []


def test_what_the_api_refuses(client, auth_headers, api):
    admin = auth_headers("root")
    assert client.post("/containers/web/snapshots", json={"name": "a"}, headers=admin).status_code == 422
    assert (
        client.post(
            "/containers/web/snapshots", json={"name": "ok"}, headers=auth_headers("eve", role="viewer")
        ).status_code
        == 403
    )
    assert client.post("/containers/nope/snapshots", json={"name": "ok"}, headers=admin).status_code == 404
    assert client.post("/containers/web/snapshots/none/restore", headers=admin).status_code == 404
    assert client.delete("/containers/web/snapshots/none", headers=admin).status_code == 404

    container_snapshots.create("web", "clean")
    api["web"].active = True
    taken = client.post("/containers/web/snapshots", json={"name": "other"}, headers=admin)
    assert taken.status_code == 409 and "Stop the container" in taken.json()["detail"]
    restored = client.post("/containers/web/snapshots/clean/restore", headers=admin)
    assert restored.status_code == 409 and "Stop the container" in restored.json()["detail"]
    api["web"].active = False
    again = client.post("/containers/web/snapshots", json={"name": "clean"}, headers=admin)
    assert again.status_code == 409 and "already exists" in again.json()["detail"]


def test_a_container_busy_with_a_snapshot_refuses_the_rest(client, auth_headers, api):
    admin = auth_headers("root")
    container_snapshots.create("web", "clean")
    with container_snapshots.claim("web", "a snapshot restore"):
        for method, path, body in (
            ("post", "/containers/web/start", None),
            ("post", "/containers/web/snapshots", {"name": "other"}),
            ("post", "/containers/web/snapshots/clean/restore", None),
            ("delete", "/containers/web/snapshots/clean", None),
            ("post", "/containers/web/backups", None),
            ("post", "/containers/web/clone", {"new_name": "web-2"}),
            ("post", "/containers/web/rename", {"new_name": "www"}),
            ("delete", "/containers/web?confirm=true", None),
        ):
            r = getattr(client, method)(path, headers=admin, **({"json": body} if body else {}))
            assert r.status_code == 409 and "busy" in r.json()["detail"], (path, r.status_code, r.text)
    assert not api["web"].started
    r = client.post("/containers/web/start", headers=admin)
    assert r.status_code == 200 and api["web"].started, "free again once the operation ends"
    assert container_snapshots.busy("web") is None


def test_a_container_deleted_takes_its_snapshots(client, auth_headers, api, place, monkeypatch):
    container_snapshots.create("web", "clean")

    class Doomed(Domain):
        def destroy(self):
            pass

        def undefine(self):
            pass

        def XMLDesc(self, flags=0):
            return ET.tostring(
                ET.fromstring("<domain type='lxc'><name>web</name><devices/></domain>"), encoding="unicode"
            )

    api["web"] = Doomed("web")
    monkeypatch.setattr(containers, "delete_container_rootfs", lambda name: shutil.rmtree(place / name))
    assert client.delete("/containers/web?confirm=true", headers=auth_headers("root")).status_code == 200
    assert not (place / ".snapshots" / "web").exists() and not (place / "web").exists()
