"""Image gallery (app/core/image_gallery.py, #348): an image lands in the imported disks only when the distribution's
checksum matches and it is a qcow2 image; anything else fails the task and leaves nothing behind."""

import hashlib
import io

import pytest

from app.core import disk_library, image_gallery, tasks

IMAGE = image_gallery.QCOW2_MAGIC + b"\x00" * 5000


class _Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture()
def web(monkeypatch):
    """The distributions' servers: {url: bytes}. The library is the suite's (HYPERLITE_VAR_DIR): emptied of the
    gallery's files first."""
    for image_id in image_gallery.CATALOG:
        (disk_library.IMPORTS / image_gallery.file_name(image_id)).unlink(missing_ok=True)
    served = {}
    monkeypatch.setattr(image_gallery, "_open", lambda url: _Response(served[url]))
    monkeypatch.setattr(image_gallery, "start_thread", lambda fn, *args, task_id=None, name=None: fn(*args))
    return served


def _serve(web, image_id, body=IMAGE, listing=None):
    entry = image_gallery.CATALOG[image_id]
    filename = entry["url"].rsplit("/", 1)[1]
    digest = hashlib.new(entry["algo"], body).hexdigest()
    web[entry["url"]] = body
    web[entry["checksums"]] = (listing if listing is not None else f"{digest}  {filename}\n").encode()


def test_checksum_lists_in_both_styles():
    h = "a" * 64
    assert image_gallery.expected_hash(f"{h} *noble.img\n", "noble.img") == h
    assert image_gallery.expected_hash(f"# comment\nSHA256 (Rocky.qcow2) = {h.upper()}\n", "Rocky.qcow2") == h
    assert image_gallery.expected_hash(f"{h}  other.img\n", "noble.img") is None


def test_a_matching_image_lands_in_the_imported_disks(database, web):
    _serve(web, "debian-12")
    task_id = image_gallery.start_download("debian-12", "admin")
    assert tasks.task_status(task_id) == "succeeded"
    assert (disk_library.IMPORTS / "debian-12-cloud.qcow2").read_bytes() == IMAGE
    entry = next(x for x in image_gallery.catalog() if x["id"] == "debian-12")
    assert entry["downloaded"] and entry["size"] == len(IMAGE)
    assert not any(disk_library.SPOOL.glob("upload-*.part"))


@pytest.mark.parametrize(
    ("body", "listing", "why"),
    [
        (IMAGE, "0" * 128 + "  debian-12-genericcloud-amd64.qcow2\n", "checksum does not match"),
        (IMAGE, "", "not in the distribution's checksum list"),
        (b"<html>not found</html>", None, "not a qcow2"),
    ],
)
def test_anything_else_fails_and_leaves_nothing(database, web, body, listing, why):
    _serve(web, "debian-12", body=body, listing=listing)
    task_id = image_gallery.start_download("debian-12", "admin")
    assert tasks.task_status(task_id) == "failed"
    assert not (disk_library.IMPORTS / "debian-12-cloud.qcow2").exists()
    assert not any(disk_library.SPOOL.glob("upload-*.part"))


def test_the_api_lists_the_gallery_and_only_an_admin_downloads(client, auth_headers, web):
    _serve(web, "ubuntu-24.04")
    admin, viewer = auth_headers("root"), auth_headers("eve", role="viewer")
    ids = [x["id"] for x in client.get("/image-gallery", headers=viewer).json()["items"]]
    assert "ubuntu-24.04" in ids and "rocky-9" in ids
    assert client.post("/image-gallery/ubuntu-24.04/download", headers=viewer).status_code == 403
    assert client.post("/image-gallery/nope/download", headers=admin).status_code == 404
    r = client.post("/image-gallery/ubuntu-24.04/download", headers=admin)
    assert r.status_code == 202 and r.json()["file"] == "ubuntu-24.04-cloud.qcow2"
