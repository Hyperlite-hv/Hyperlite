"""What a code review of 1.28.1 found (ECC's review agents in a sandbox, each finding checked against the code), one
test per fix, where the module's own tests did not already hold it."""

import xml.etree.ElementTree as ET

import pytest

from app.core import disk_images, linked_disks, recycle_bin


def _disk(driver=None):
    xml = "<disk type='file' device='disk'>"
    if driver is not None:
        xml += f"<driver name='qemu' type='{driver}'/>"
    return ET.fromstring(xml + "<source file='/x'/></disk>")


def test_a_copy_reads_a_disk_in_the_format_its_definition_names():
    assert disk_images.declared_format(_disk("qcow2")) == "qcow2"
    assert disk_images.declared_format(_disk("raw")) == "raw"
    assert disk_images.declared_format(_disk()) == "raw", "no driver type: QEMU reads it raw, so does the copy"
    with pytest.raises(disk_images.UnsafeImage):
        disk_images.declared_format(_disk("vmdk"))


def test_a_base_image_or_a_binned_disk_is_never_given_to_a_vm(monkeypatch):
    monkeypatch.setattr(linked_disks, "dependents", lambda path, conn: ["web1", "web2"] if path == "/p/base" else [])
    monkeypatch.setattr(recycle_bin, "binned_paths", lambda: {"/p/old.qcow2"})
    assert "web1, web2" in linked_disks.refusal("/p/base", None)
    assert "recycle bin" in linked_disks.refusal("/p/old.qcow2", None)
    assert linked_disks.refusal("/p/free.qcow2", None) is None


def test_the_host_network_routes_go_to_the_member_named_by_node():
    """The dashboard sends ?node= to manage another member's interfaces: without the forwarding, the node the browser
    is connected to applied the change to its own network, and reported it as the other's."""
    from app.core import cluster_view
    from app.routers.host_network import router

    assert any(d.dependency is cluster_view.on_node for d in router.dependencies)


def test_a_backup_time_is_one_directory_name_never_a_path(tmp_path):
    from app.core import backup_store

    for bad in ("../../../../etc/cron.d", "a/b", "..", "", "x" * 41, None):
        with pytest.raises(ValueError):
            backup_store.check_stamp(bad)
    for good in ("2026-11-16T10:00:00Z", "2026-11-16T10:00:00+00:00"):
        assert backup_store.check_stamp(good) == good


def test_a_remotes_bad_backup_entries_are_skipped_not_used_as_paths(monkeypatch):
    from app.core import backup_remotes

    vm = "6f1c2a3b-0000-4000-8000-000000000001"
    good = {"vm_uuid": vm, "time": "2026-11-16T10:00:00Z", "manifest": {"disks": {"vda": {}}, "files": {}}}
    export = {
        "config": {"id": "s", "chunk_size": 4194304},
        "backups": [
            good,
            {**good, "time": "../../../../etc/cron.d"},
            {**good, "vm_uuid": "not-a-uuid"},
            {**good, "manifest": {"disks": {}, "files": {"../x": {}}}},
        ],
    }

    class Client:
        def json(self, path):
            return export

    monkeypatch.setattr(backup_remotes, "get", lambda name: {})
    monkeypatch.setattr(backup_remotes, "_client", lambda remote: Client())
    store = backup_remotes.RemoteStore("far", "s")
    assert store.backups() == [(vm, "2026-11-16T10:00:00Z", good["manifest"])]


def test_a_live_restore_never_reads_through_an_image_that_names_another_file(tmp_path, monkeypatch):
    from app.core import live_restore

    image = tmp_path / "point" / "vda.qcow2"
    image.parent.mkdir()
    image.write_bytes(b"x")

    def unsafe(path, within=None):
        raise disk_images.UnsafeImage("based on another file")

    monkeypatch.setattr(disk_images, "check", unsafe)
    monkeypatch.setattr(live_restore.subprocess, "run", lambda *a, **k: pytest.fail("overlay made"))
    with pytest.raises(live_restore.LiveRestoreError):
        live_restore.overlay(image, tmp_path / "new.qcow2")


def test_a_source_backup_dated_in_the_future_never_ranks_in_the_copy():
    from datetime import UTC, datetime, timedelta

    from app.core import backup_store_sync

    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    assert backup_store_sync._plausible({"time": "2026-11-16T11:00:00+00:00"}, "s", now)
    assert not backup_store_sync._plausible({"time": (now + timedelta(days=30)).isoformat()}, "s", now)
    assert not backup_store_sync._plausible({"time": "garbage"}, "s", now)


def test_an_encrypted_store_whose_directory_says_otherwise_is_refused():
    from app.core import backup_stores

    class Plain:
        encrypted = False

    with pytest.raises(backup_stores.StoreError, match="recorded as encrypted"):
        backup_stores._refuse_downgrade(Plain(), {"name": "main", "encrypted": 1})
    backup_stores._refuse_downgrade(Plain(), {"name": "main", "encrypted": 0})
    backup_stores._refuse_downgrade(Plain(), None)


def test_whoever_changes_a_patch_plan_becomes_its_author(database):
    from app.core import patch_plans

    fields = {"name": "nightly", "target_kind": "vms", "target": ["db1"], "days": [0], "at_time": "03:00",
              "duration_min": 60}  # fmt: skip
    plan = patch_plans.save(fields, "admin1")
    assert plan["created_by"] == "admin1"
    changed = patch_plans.save({**fields, "target_kind": "tag", "target": "prod", "reboot": True}, "bob", plan["id"])
    assert changed["created_by"] == "bob", "the plan acts with the rights of who chose its new targets"


def test_protections_hold_on_restore_move_and_delete_whatever_the_url(database, monkeypatch):
    """The protections were checked on the URLs about the VM only: a restore over it, a move to another site and the
    deletions VM groups make went through. They are checked by the operations themselves."""
    from fastapi import HTTPException

    from app.core import backups, protections, site_move
    from app.routers.vms import lifecycle

    protections.put("vm", "db1", "keep", "the billing database", "root")
    with pytest.raises(site_move.MoveError, match="kept from deletion"):
        site_move._refuse_protected("db1")
    monkeypatch.setattr(backups, "_store", lambda: type("S", (), {"get": lambda self, i: {
        "status": "succeeded", "vm_name": "db1"}})())  # fmt: skip
    with pytest.raises(RuntimeError, match="kept from deletion"):
        backups._restore_backup(1, "overwrite")
    with pytest.raises(HTTPException) as e:
        lifecycle._delete_vm("db1", True, None, {"username": "root", "role": "admin"})
    assert e.value.status_code == 409


@pytest.mark.parametrize(
    ("machine", "family"), [("pc-q35-9.2", "q35"), ("q35", "q35"), ("pc-i440fx-8.2", "pc"), ("pc", "pc")]
)
def test_ha_keeps_the_machine_family_a_vm_was_installed_on(machine, family):
    from app.core import ha

    xml = (f"<domain><os><type machine='{machine}'>hvm</type></os><cpu mode='host-passthrough'>"
           "<topology sockets='1' cores='4' threads='2'/></cpu></domain>")  # fmt: skip
    root = ET.fromstring(ha._portable_xml(xml))
    assert root.find("os/type").get("machine") == family
    assert root.find("cpu").get("mode") == "host-model"
    assert root.find("cpu/topology").get("cores") == "4"


def test_the_daemon_refuses_an_api_token_what_an_account_does_to_itself():
    """hyperlite-daemon holds even when the API is not to be trusted: a token, an administrator's included, never mints
    tokens, changes the password or the second factors of its own account; it revokes only itself."""
    from types import SimpleNamespace

    from app.daemon import needs
    from app.daemon.ops import access

    admin_token = {"username": "ana", "role": "admin", "api_token_id": 7}
    session = {"username": "ana", "role": "admin"}
    own = needs.own()
    assert own.refusal(admin_token, SimpleNamespace(username="ana"))
    assert own.refusal(session, SimpleNamespace(username="ana")) is None
    assert own.refusal(admin_token, SimpleNamespace(username="bob")) is None, "an admin's token on other accounts"
    password = needs.by_rule(access._own_password)
    assert password.refusal(admin_token, SimpleNamespace(username="ana", role=None))
    revoke = needs.by_rule(access._own_token_revoke)
    assert revoke.refusal(admin_token, SimpleNamespace(username="ana", token_id=7)) is None
    assert revoke.refusal(admin_token, SimpleNamespace(username="ana", token_id=8))


def test_the_daemon_puts_a_vm_in_a_pool_only_for_who_may_or_just_made_it(database, make_user, monkeypatch):
    from types import SimpleNamespace

    from app.core import permissions
    from app.daemon.ops import access

    make_user("alice", "viewer")
    alice = {"username": "alice", "role": "viewer", "id": 2}
    monkeypatch.setattr(permissions, "has_scoped_privilege", lambda *a: True)
    monkeypatch.setattr(permissions, "has_privilege", lambda *a: False)
    monkeypatch.setattr(permissions, "get_vm_pools", lambda vm: [])
    assert "bob-db" in access._may_allocate_in_pool(alice, SimpleNamespace(pool_id=7, vm_name="bob-db"))
    from app.core.tasks import create_task

    create_task("create_vm", "alice-web", node=None, username="alice")
    assert access._may_allocate_in_pool(alice, SimpleNamespace(pool_id=7, vm_name="alice-web")) is None


def test_the_terminal_box_sees_no_process_outside_it(monkeypatch):
    """The relay and the tmux clients run outside the box as its own uid: the box gets its own PID namespace (systemd
    257 and later), and the relay is not dumpable (scripts/workbench/relay.py)."""
    from pathlib import Path

    from app.core import workbench_box

    monkeypatch.setattr(workbench_box, "_systemd_version", lambda: 257)
    assert "PrivatePIDs=yes" in workbench_box.box_properties("0123456789abcdef", 700001, {})
    monkeypatch.setattr(workbench_box, "_systemd_version", lambda: 252)
    assert "PrivatePIDs=yes" not in workbench_box.box_properties("0123456789abcdef", 700001, {})
    relay = (Path(__file__).parent.parent / "scripts/workbench/relay.py").read_text()
    assert "PR_SET_DUMPABLE" in relay and "_not_dumpable()" in relay


def test_the_assistants_tools_take_plain_names_only():
    import asyncio

    from app.core import assistant

    async def get(path, params):
        return {"path": path}

    for bad in ({"name": "x/identity"}, {"name": "../../auth/tokens"}, {"name": "web", "node": "a/b"}):
        with pytest.raises(assistant.AssistantError):
            asyncio.run(assistant.run_tool(get, "get_vm", bad))
    with pytest.raises(assistant.AssistantError):
        asyncio.run(assistant.run_tool(get, "availability", {"kind": "vms/x", "name": "web"}))
    assert asyncio.run(assistant.run_tool(get, "get_vm", {"name": "web-1"})) == {"path": "/vms/web-1"}


def test_the_assistants_key_never_goes_to_another_service(database, monkeypatch):
    from app.core import assistant, http_safety

    monkeypatch.setattr(http_safety, "refuse_link_local", lambda url: url)
    assistant.set_settings({"provider": "openai", "base_url": "http://10.0.0.5:11434/v1", "api_key": "k1"})
    assert assistant.settings(with_secret=True)["api_key"]
    assistant.set_settings({"base_url": "http://elsewhere.example:8080/v1"})
    assert assistant.settings(with_secret=True)["api_key"] == "", "the key is given again for a new service"


def test_a_metadata_address_is_never_an_endpoint():
    from app.core import http_safety

    with pytest.raises(ValueError, match="link-local"):
        http_safety.refuse_link_local("http://169.254.169.254/latest/meta-data/")
    assert http_safety.refuse_link_local("http://127.0.0.1:11434/v1")


def test_a_delegated_token_reads_no_secret_of_the_vault():
    from app.core import vault

    secret = {"readers": ["user:ana"]}
    assert not vault.may_read({"username": "ana", "role": "delegated", "token_scope": {"role": "viewer"}}, secret)
    assert not vault.may_read({"username": "ana", "role": "admin", "token_scope": {"role": "viewer"}}, secret)


def test_a_replicated_row_names_only_columns_the_table_has(database):
    import sqlite3
    from types import SimpleNamespace

    from app.repositories.cfs import inbound

    table = SimpleNamespace(name="dashboards", pk=("id",))
    with database.get_conn() as db, pytest.raises(sqlite3.OperationalError, match="no column"):
        inbound._upsert(db, table, {"id": "x", 'name" = 1; DROP TABLE users; --': 1})


def test_the_daemon_checks_the_pool_the_network_and_the_backup_directory_too(database, monkeypatch):
    from types import SimpleNamespace

    from app.core import permissions
    from app.daemon import registry
    from app.daemon.ops import (
        backup,  # noqa: F401  (registers its operations)
        vm,  # noqa: F401
    )

    def can(user, name, privilege):
        return privilege == "vm.hardware" or privilege == "vm.snapshot"

    monkeypatch.setattr(permissions, "has_privilege", can)
    monkeypatch.setattr(permissions, "has_scoped_privilege", lambda *a: False)
    ana = {"username": "ana", "role": "operator"}
    attach = registry.NEEDS["vm.attach_disk"]
    assert attach.refusal(ana, SimpleNamespace(vm="web", pool="secret-pool"))
    nic = registry.NEEDS["vm.attach_interface"]
    assert nic.refusal(ana, SimpleNamespace(vm="web", network="admin-net"))
    run = registry.NEEDS["backup.run"]
    assert run.refusal(ana, SimpleNamespace(vm="web", target=None)) is None
    assert (
        run.refusal(ana, SimpleNamespace(vm="web", target="/etc"))
        == "Only an administrator chooses the backup directory"
    )
