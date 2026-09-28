"""Creating a VM from an ISO image stored on another node: checks first, then copy, then creation.

The copy uses the simulated nodes of test_iso_share; the creation itself is replaced by a recorder, since what is
tested here is the orchestration, not the libvirt domain building.
"""

import time

import pytest
from fastapi import HTTPException

BASE = {"name": "vm-remote-iso", "vcpu": 1, "memory_mb": 1024, "disks": [{"size_gb": 10}], "guest_os": "other"}


@pytest.fixture()
def creations(monkeypatch):
    from app.routers.vms import create

    calls = []

    def fake_create(payload, user, pending=frozenset(), check_only=False):
        calls.append({"iso": payload.iso, "pending": set(pending), "check_only": check_only})
        if payload.name == "taken":
            raise HTTPException(status_code=422, detail=["A VM named 'taken' already exists"])
        return None if check_only else {"name": payload.name}

    monkeypatch.setattr(create, "_create_vm", fake_create)
    return calls


def _wait(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_a_local_image_creates_the_vm_directly(cluster, creations, client, auth_headers):
    (cluster["local"] / "debian.iso").write_bytes(b"d")
    cluster["add_node"]("antho", "10.0.0.2")
    r = client.post("/vms", json={**BASE, "iso": "debian.iso", "iso_node": "local"}, headers=auth_headers("admin"))
    assert r.status_code == 201, r.text
    assert creations == [{"iso": "debian.iso", "pending": set(), "check_only": False}]
    assert cluster["scp_calls"] == []


def test_an_image_from_another_node_is_copied_here_then_the_vm_is_created(cluster, creations, client, auth_headers):
    (cluster["add_node"]("antho", "10.0.0.2") / "ubuntu.iso").write_bytes(b"u" * 64)
    r = client.post("/vms", json={**BASE, "iso": "ubuntu.iso", "iso_node": "antho"}, headers=auth_headers("admin"))
    assert r.status_code == 202, r.text
    assert [t["node"] for t in r.json()["copie_iso"]] == ["local"]
    # Everything is checked before the copy, with the image counted as on its way.
    assert creations[0] == {"iso": "ubuntu.iso", "pending": {"ubuntu.iso"}, "check_only": True}
    _wait(lambda: len(creations) == 2)
    assert creations[1] == {"iso": "ubuntu.iso", "pending": set(), "check_only": False}
    assert (cluster["local"] / "ubuntu.iso").read_bytes() == b"u" * 64


def test_an_image_already_copied_here_is_not_copied_again(cluster, creations, client, auth_headers):
    (cluster["add_node"]("antho", "10.0.0.2") / "ubuntu.iso").write_bytes(b"u")
    (cluster["local"] / "ubuntu.iso").write_bytes(b"u")
    r = client.post("/vms", json={**BASE, "iso": "ubuntu.iso", "iso_node": "antho"}, headers=auth_headers("admin"))
    assert r.status_code == 201, r.text
    assert cluster["scp_calls"] == []


def test_a_form_error_is_reported_before_any_copy(cluster, creations, client, auth_headers):
    (cluster["add_node"]("antho", "10.0.0.2") / "ubuntu.iso").write_bytes(b"u")
    r = client.post(
        "/vms", json={**BASE, "name": "taken", "iso": "ubuntu.iso", "iso_node": "antho"}, headers=auth_headers("admin")
    )
    assert r.status_code == 422
    assert "already exists" in r.text
    assert cluster["scp_calls"] == []
    assert not (cluster["local"] / "ubuntu.iso").exists()


@pytest.mark.parametrize(
    ("iso", "node", "status"), [("missing.iso", "antho", 422), ("ubuntu.iso", "ghost", 404), ("../x.iso", "antho", 422)]
)
def test_the_image_and_its_node_are_checked_before_any_copy(
    cluster, creations, client, auth_headers, iso, node, status
):
    (cluster["add_node"]("antho", "10.0.0.2") / "ubuntu.iso").write_bytes(b"u")
    r = client.post("/vms", json={**BASE, "iso": iso, "iso_node": node}, headers=auth_headers("admin"))
    assert r.status_code == status, r.text
    assert cluster["scp_calls"] == []
    assert all(c["check_only"] for c in creations)


def test_a_failed_copy_creates_no_vm(cluster, creations, client, auth_headers, monkeypatch):
    from app.core import iso_share

    (cluster["add_node"]("antho", "10.0.0.2") / "ubuntu.iso").write_bytes(b"u")

    def broken_scp(args, timeout=None):
        raise RuntimeError("Connection reset by peer")

    monkeypatch.setattr(iso_share, "_scp", broken_scp)
    r = client.post("/vms", json={**BASE, "iso": "ubuntu.iso", "iso_node": "antho"}, headers=auth_headers("admin"))
    assert r.status_code == 202
    _wait(lambda: not iso_share._active)
    time.sleep(0.1)
    assert [c["check_only"] for c in creations] == [True]
    assert not (cluster["local"] / "ubuntu.iso").exists()


def test_the_windows_driver_image_can_come_from_another_node_too(cluster, creations, client, auth_headers):
    (cluster["local"] / "win.iso").write_bytes(b"w")
    (cluster["add_node"]("antho", "10.0.0.2") / "virtio-win.iso").write_bytes(b"v" * 8)
    payload = {
        **BASE,
        "iso": "win.iso",
        "drivers_iso": "virtio-win.iso",
        "drivers_iso_node": "antho",
        "guest_os": "windows",
    }
    r = client.post("/vms", json=payload, headers=auth_headers("admin"))
    assert r.status_code == 202, r.text
    _wait(lambda: len(creations) == 2)
    assert (cluster["local"] / "virtio-win.iso").read_bytes() == b"v" * 8
