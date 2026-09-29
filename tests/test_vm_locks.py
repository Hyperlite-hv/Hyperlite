"""One operation at a time on a VM: a second one is refused at once, and a claim is always released."""

import pytest

from app.core import vm_locks


def test_a_busy_vm_refuses_a_second_operation_until_released():
    first = vm_locks.claim("vm1", "a backup")
    with pytest.raises(vm_locks.VmBusy) as busy:
        vm_locks.claim("vm1", "a restore")
    assert busy.value.running == "a backup"
    assert vm_locks.running("vm1") == "a backup"
    first.release()
    first.release()  # idempotent: a second release must not free someone else's claim
    second = vm_locks.claim("vm1", "a restore")
    assert vm_locks.running("vm1") == "a restore"
    first.release()
    assert vm_locks.running("vm1") == "a restore"
    second.release()
    assert vm_locks.running("vm1") is None


def test_the_same_name_on_two_nodes_is_two_vms():
    local = vm_locks.claim("web", "a backup")
    remote = vm_locks.claim("web", "a migration", node="node-b")
    local.release()
    remote.release()


def test_released_after_frees_the_vm_even_when_the_work_fails():
    held = vm_locks.claim("vm2", "a snapshot")

    def boom():
        raise OSError("disk full")

    with pytest.raises(OSError):
        vm_locks.released_after(held, boom)()
    assert vm_locks.running("vm2") is None


def test_an_endpoint_answers_409_while_the_vm_is_busy(client, auth_headers, database):
    headers = auth_headers("alice")
    held = vm_locks.claim("vm3", "a backup restore")
    try:
        r = client.delete("/vms/vm3?confirm=true", headers=headers)
        assert r.status_code == 409
        assert "a backup restore" in r.json()["detail"]
    finally:
        held.release()
