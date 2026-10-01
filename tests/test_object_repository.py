"""The object repository (control plane v2, lot 9): what Hyperlite keeps beside a VM or a container, run against every
backend (SQLite today). Renames are covered through app/core/renaming.py in test_basic_actions.py."""

import asyncio

import pytest


@pytest.fixture(params=["sqlite"])
def repo(request, database):
    from app.repositories.sqlite.objects import SqliteObjectRepository

    return SqliteObjectRepository()


def run(coro):
    return asyncio.run(coro)


def test_vm_side_records(repo):
    store = repo.sync
    store.set_vm_ssh_user("web", "ubuntu")
    store.set_vm_ssh_user("web", "debian")
    store.set_vm_os_label("web", "Debian 13")
    store.mark_provisioning("web", "debian", "t0", "task-1")
    assert (store.vm_ssh_user("web"), store.vm_os_label("web")) == ("debian", "Debian 13")
    assert store.provisioning("web") == {"os_family": "debian", "started_at": "t0", "task_id": "task-1"}
    store.save_cloudinit("web", "ops", '["ssh-ed25519 AAAA"]', "t1")
    assert store.cloudinit("web")["username"] == "ops"
    for delete in (
        store.delete_vm_ssh_user,
        store.delete_vm_os_label,
        store.clear_provisioning,
        store.delete_cloudinit,
    ):
        delete("web")
    assert store.vm_ssh_user("web") is None and store.provisioning("web") is None and store.cloudinit("web") is None


def test_inactivity_policy(repo):
    store = repo.sync
    store.set_auto_cleanup("web", 30, "t0")
    store.mark_cleanup_warned("web", "t1")
    assert store.auto_cleanup("web")["warned_at"] == "t1"
    store.touch_activity("web", "t2")
    assert store.auto_cleanup("web") == {
        "inactive_days": 30,
        "last_active_at": "t2",
        "warned_at": None,
        "created_at": "t0",
    }
    store.touch_activity("ghost", "t2")  # no policy: nothing happens
    assert [r["vm_name"] for r in store.all_auto_cleanup()] == ["web"]
    store.delete_auto_cleanup("web")
    assert store.all_auto_cleanup() == []


def test_start_at_boot(repo):
    store = repo.sync
    store.set_boot_setting("local", "db", 1, 1, 0)
    store.set_boot_setting("local", "web", 1, 2, 30)
    store.set_boot_setting("local", "off", 0, None, 0)
    assert run(repo.boot_setting("local", "web")) == {"autostart": 1, "boot_order": 2, "delay_s": 30}
    assert sorted(r["vm_name"] for r in store.autostart_vms("local")) == ["db", "web"]
    assert store.move_boot_setting("web", "local", "n2") and not store.move_boot_setting("web", "local", "n2")
    store.delete_boot_setting("local", "db")
    assert store.autostart_vms("local") == [] and store.boot_setting("n2", "web")["delay_s"] == 30
    assert store.claim_boot("local", "b1") and not store.claim_boot("local", "b1") and store.claim_boot("local", "b2")


def test_container_side_records(repo):
    store = repo.sync
    store.set_container_ssh_user("pg", "root")
    store.set_container_app("pg", "postgres:17", '{"env": {}}', "10.0.0.5", "default")
    store.set_container_storage("pg", "fast", "/srv/fast")
    assert store.container_ssh_user("pg") == "root"
    assert store.container_app("pg")["image"] == "postgres:17" and store.container_storage("pg")["pool"] == "fast"
    store.delete_container_ssh_user("pg")
    store.delete_container_app("pg")
    store.delete_container_storage("pg")
    assert store.container_ssh_user("pg") is None and store.container_app("pg") is None
    assert store.container_storage("pg") is None


def test_notes_and_tags(repo):
    store = repo.sync
    store.put_meta("vm", "local", "web", "front", '["prod"]')
    store.put_meta("node", "", "n2", "", '["rack"]')
    assert run(repo.meta("vm", "local", "web")) == {"notes": "front", "tags": '["prod"]'}
    assert [r["name"] for r in run(repo.all_meta())] == ["n2", "web"]
    assert [r["name"] for r in store.all_meta("vm")] == ["web"]
    assert store.move_vm_meta("web", "local", "n2") and store.meta("vm", "n2", "web")
    store.delete_meta("vm", "n2", "web")
    assert store.meta("vm", "n2", "web") is None
