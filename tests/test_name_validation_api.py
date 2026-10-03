"""Resource names are validated before anything is written, and the error names the resource."""

import io

import libvirt
import pytest

from app.routers import isos


def test_a_network_name_error_says_network(client, auth_headers):
    r = client.post("/networks", json={"name": "bad name!", "mode": "nat"}, headers=auth_headers("alice"))
    assert r.status_code == 422
    assert "Invalid network name" in str(r.json()["detail"])


def test_an_invalid_template_name_is_refused(client, auth_headers):
    r = client.post("/templates/from-vm/vm1", json={"template_name": "../escape"}, headers=auth_headers("alice"))
    assert r.status_code == 422
    assert r.json()["detail"].startswith("Invalid template name")


@pytest.fixture()
def iso_dir(tmp_path, monkeypatch):
    target = tmp_path / "isos"
    target.mkdir()
    monkeypatch.setattr(isos, "ISOS_DIR", target)

    def _no_libvirt(*_a, **_k):
        raise libvirt.libvirtError("no libvirt in the test suite")

    monkeypatch.setattr(isos, "open_conn", _no_libvirt)
    return target


@pytest.mark.parametrize("filename", ["bad name!.iso", "-leading.iso", "x\n.iso"])
def test_an_invalid_iso_name_is_refused(client, auth_headers, iso_dir, filename):
    files = {"file": (filename, io.BytesIO(b"data"), "application/octet-stream")}
    r = client.post("/isos", files=files, headers=auth_headers("alice"))
    assert r.status_code == 422
    assert list(iso_dir.iterdir()) == []


def test_a_distribution_iso_name_is_accepted(client, auth_headers, iso_dir):
    name = "debian-13.1.0-amd64_netinst+firmware.iso"
    files = {"file": (name, io.BytesIO(b"data"), "application/octet-stream")}
    r = client.post("/isos", files=files, headers=auth_headers("alice"))
    assert r.status_code == 201, r.text
    assert (iso_dir / name).read_bytes() == b"data"


def test_an_upload_never_replaces_an_iso(client, auth_headers, iso_dir):
    """It replaced it silently, even one in a VM's CD-ROM drive."""
    files = lambda data: {"file": ("debian.iso", io.BytesIO(data), "application/octet-stream")}  # noqa: E731
    admin = auth_headers("alice")
    assert client.post("/isos", files=files(b"first"), headers=admin).status_code == 201
    r = client.post("/isos", files=files(b"second"), headers=admin)
    assert r.status_code == 409 and "already exists" in r.json()["detail"]
    assert (iso_dir / "debian.iso").read_bytes() == b"first"


def test_an_interrupted_upload_leaves_nothing_in_the_library(client, auth_headers, iso_dir, monkeypatch):
    """It was written under its final name: a truncated ISO stayed in the library."""
    from app.routers import isos

    def _fail(source, out, length=0):
        out.write(b"half")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(isos.shutil, "copyfileobj", _fail)
    files = {"file": ("debian.iso", io.BytesIO(b"data"), "application/octet-stream")}
    r = client.post("/isos", files=files, headers=auth_headers("alice"))
    assert r.status_code == 500
    assert list(iso_dir.iterdir()) == []
