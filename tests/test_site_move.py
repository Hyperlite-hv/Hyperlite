"""A stopped VM moved to another cluster through the API (docs/design/multi-cluster.md, section 9.1): the source's
task and the target's routes, both this test's application, the pinned connection replaced by the test client
(tests/test_sites.py). hyperlite-daemon's transfer operations are stand-ins over pipes, as the real ones hand them
over: what the source's export gives arrives byte for byte in the target's import, then the VM is defined there and
deleted here. The real operations on real disks are tests/test_site_transfer_qemu.py's."""

import os
import struct
import threading
import time

import pytest

from app.core import privileged, site_stream, sites
from app.daemon.registry import DaemonError
from tests.test_sites import PIN, load, manager, viewer, wire  # noqa: F401 (fixtures)

PIPE = struct.Struct(">BQQ")
SIZE = 3 * site_stream.FRAME + 4096
DISK = bytes(os.urandom(5000)) + bytes(SIZE - 5000 - 100) + b"z" * 100
SPEC = {
    "name": "web", "uuid": "6f1c1c39-6b0b-4b8a-9d4a-3f0e8e2b9a11", "memory_mb": 1024, "current_memory_mb": 1024,
    "vcpus": 1, "firmware": "bios", "machine": "pc", "clock": "utc", "description": "", "cpu": {"mode": "host-model"},
    "disks": [{"dev": "sda", "bus": "scsi", "size": SIZE, "format": "qcow2", "cache": None, "discard": None,
               "serial": None, "boot": 1, "iotune": {}}],
    "cdroms": [], "nics": [{"mac": "52:54:00:00:00:01", "model": "virtio", "network": "lan", "boot": None}],
    "graphics": "vnc", "video": "virtio", "agent": True, "balloon": True, "rng": False,
}  # fmt: skip


@pytest.fixture()
def daemon(monkeypatch):
    """The transfer operations as stand-ins: the source's disk DISK, the target's written into `written`."""
    state = {"written": bytearray(SIZE), "calls": [], "threads": []}

    def export():
        r, w = os.pipe()

        def fill():
            with os.fdopen(w, "wb") as f:
                for offset, length, data in ((0, 5000, DISK[:5000]), (SIZE - 100, 100, DISK[-100:])):
                    if offset == SIZE - 100:
                        f.write(PIPE.pack(3, 5000, SIZE - 5100))
                    f.write(PIPE.pack(1, offset, length) + data)
                f.write(PIPE.pack(0, SIZE, 0))

        threading.Thread(target=fill, daemon=True).start()
        return {"size": SIZE}, [r]

    def import_():
        r, w = os.pipe()

        def drain():
            with os.fdopen(r, "rb") as f:
                while True:
                    head = f.read(PIPE.size)
                    if len(head) < PIPE.size:  # closed without its end: refused by the API
                        return
                    kind, offset, length = PIPE.unpack(head)
                    if kind == 0:
                        state["received"] = True
                        return
                    if kind == 1:
                        state["written"][offset : offset + length] = f.read(length)

        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
        state["threads"].append(thread)
        return {"size": SIZE}, [w]

    def run_fds(op, user=None, timeout=None, **args):
        state["calls"].append(op)
        if op == "transfer.describe":
            return {"spec": SPEC, "left": ["CPU pinning (it names this host's CPUs)"], "allocated": {"sda": 5100}}, []
        if op == "transfer.export":
            return export()
        if op == "transfer.prepare":
            assert args["spec"]["name"] == "web" and args["pools"] == ["fast"]
            return {"incoming": "a" * 32, "disks": [{"dev": "sda", "size": SIZE}]}, []
        if op == "transfer.import":
            return import_()
        if op == "transfer.define":
            for thread in state["threads"]:
                thread.join(5)
            if not state.get("received"):
                raise DaemonError("refused", "Not every disk was written")
            assert args["networks"] == ["prod"]
            return {"name": "web", "started": False}, []
        if op == "transfer.abort":
            return None, []
        if op == "vm.delete":
            return {"kept": {}}, []
        raise AssertionError(op)

    monkeypatch.setattr(privileged, "run_fds", run_fds)
    return state


def _wait(client, headers, task_id):
    for _ in range(100):
        task = client.get(f"/tasks/{task_id}", headers=headers).json()
        if task["status"] != "running":
            return task
        time.sleep(0.1)
    raise AssertionError("the task never ended")


def _remote(client, admin, viewer, manager):  # noqa: F811
    body = {"name": "site-b", "address": "10.0.0.7", "port": 8000, "fingerprint": PIN, "view_token": viewer,
            "manage_token": manager}  # fmt: skip
    assert client.post("/sites/remotes", json=body, headers=admin).status_code == 201


MOVE = {"remote": "site-b", "pools": ["fast"], "networks": ["prod"]}


def test_a_vm_goes_byte_for_byte_and_is_deleted_here(client, auth_headers, viewer, manager, load, wire, daemon):  # noqa: F811
    admin = auth_headers("ana")
    _remote(client, admin, viewer, manager)
    assert client.get("/sites/outgoing/web", headers=admin).json()["spec"]["name"] == "web"
    r = client.post("/sites/outgoing/web", json={**MOVE, "node": load}, headers=admin)
    assert r.status_code == 202, r.text
    task = _wait(client, admin, r.json()["task_id"])
    assert task["status"] == "succeeded", task
    assert bytes(daemon["written"]) == DISK
    assert daemon["calls"][-1] == "vm.delete" and "transfer.abort" not in daemon["calls"]
    # The disk went straight to the target node's endpoint, with the manage token.
    [put] = [x for x in wire["requests"] if x[1] == "PUT"]
    assert put[0] == "10.0.0.7" and put[3] == f"Bearer {manager}" and put[2] == f"/sites/incoming/{'a' * 32}/disks/sda"


def test_a_stream_the_target_refuses_leaves_the_vm_here(client, auth_headers, viewer, manager, load, wire, daemon):  # noqa: F811
    admin = auth_headers("ana")
    _remote(client, admin, viewer, manager)
    wire["canned"][("PUT", f"/sites/incoming/{'a' * 32}/disks/sda")] = (422, {"detail": "The disk was refused: digest"})
    task = _wait(
        client, admin, client.post("/sites/outgoing/web", json={**MOVE, "node": load}, headers=admin).json()["task_id"]
    )
    assert task["status"] == "failed" and "refused" in str(task), task
    assert "transfer.abort" in daemon["calls"] and "vm.delete" not in daemon["calls"]


def test_the_target_refuses_a_broken_stream(client, auth_headers, daemon):
    admin = auth_headers("ana")
    good = b"".join(site_stream.encode([(0, DISK[:5000])]))
    r = client.put(f"/sites/incoming/{'a' * 32}/disks/sda", content=good[:-5], headers=admin)
    assert r.status_code == 422 and "ended in the middle" in r.json()["detail"]
    r = client.put(f"/sites/incoming/{'a' * 32}/disks/sda", content=good, headers=admin)
    assert r.status_code == 200 and len(r.json()["digest"]) == 64


def test_a_move_needs_a_manage_token_and_a_viewer_cannot_send(client, auth_headers, viewer, load, wire, daemon):  # noqa: F811
    admin = auth_headers("ana")
    sites.add("site-b", "10.0.0.7", 8000, PIN, viewer, "ana")
    r = client.post("/sites/outgoing/web", json={**MOVE, "node": load}, headers=admin)
    assert r.status_code == 409 and "manage token" in r.json()["detail"]
    as_viewer = {"Authorization": f"Bearer {viewer}"}
    assert (
        client.post(
            "/sites/incoming", json={"spec": SPEC, "pools": ["x"], "allocated": [1]}, headers=as_viewer
        ).status_code
        == 403
    )
