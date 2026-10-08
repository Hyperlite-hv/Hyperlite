"""The audit log records actions, not the dashboard's polling, and does not grow for ever."""

import logging
import queue
from datetime import UTC, datetime, timedelta

from app.core import audit


def _rows(database):
    audit._AUDIT_QUEUE.join()
    with database.get_conn() as conn:
        return [tuple(r) for r in conn.execute("SELECT action, result FROM audit_log ORDER BY id")]


def test_successful_reads_are_not_audited_but_failures_and_actions_are(database):
    audit.log_action("alice", "list_vms", "vms", "succes")
    audit.log_action("alice", "get_vm", "web", "succes")
    audit.log_action("alice", "get_vm", "ghost", "echec", "VM not found")
    audit.log_action("alice", "start_vm", "web", "succes")
    assert _rows(database) == [("get_vm", "echec"), ("start_vm", "succes")]


def test_a_lost_entry_is_logged_without_what_came_from_the_request(database, monkeypatch, caplog):
    def _full(_entry):
        raise queue.Full

    monkeypatch.setattr(audit._AUDIT_QUEUE, "put_nowait", _full)
    with caplog.at_level(logging.ERROR, logger=audit.__name__):
        audit.log_action("alice", "change_password", "alice", "echec", "rejected: Tr0ub4dor&3")
    assert "change_password" in caplog.text
    assert "Tr0ub4dor" not in caplog.text and "alice" not in caplog.text


class _EmptyHost:
    """A libvirt connection with nothing on it: the suite has no hypervisor."""

    def listAllNetworks(self, *_a):
        return []

    def listAllStoragePools(self, *_a):
        return []

    def listAllDomains(self, *_a):
        return []

    def getAllDomainStats(self, *_a):
        return []

    def close(self):
        return 0


def test_the_polled_inventory_leaves_no_trace(client, auth_headers, database, monkeypatch):
    from app.routers import network, storage

    for module in (network, storage):
        monkeypatch.setattr(module, "open_conn", lambda *_a, **_k: _EmptyHost())
    monkeypatch.setattr(network, "ensure_isolated_network", lambda _conn: None)
    monkeypatch.setattr(storage, "ensure_default_pool", lambda _conn: None)
    monkeypatch.setattr(storage.zfs_storage, "list_pools", lambda: [])
    headers = auth_headers("alice")
    before = len(_rows(database))
    for path in ("/networks", "/storage"):
        assert client.get(path, headers=headers).status_code == 200
    assert len(_rows(database)) == before


def test_entries_older_than_the_retention_are_deleted(database, monkeypatch):
    now = datetime.now(UTC)
    with database.get_conn() as conn:
        for age in (400, 10):
            conn.execute(
                "INSERT INTO audit_log (timestamp, username, action, resource, result) VALUES (?, 'x', 'a', 'r', 'succes')",
                ((now - timedelta(days=age)).isoformat(),),
            )
        conn.commit()
    monkeypatch.setenv("HYPERLITE_AUDIT_RETENTION_DAYS", "365")
    assert audit.purge_old_entries(now) == 1
    monkeypatch.setenv("HYPERLITE_AUDIT_RETENTION_DAYS", "0")
    assert audit.purge_old_entries(now + timedelta(days=4000)) == 0  # 0 keeps everything


def test_the_reads_logged_before_are_removed_at_start(database):
    """102,073 of 102,926 entries of an audited host were successful reads logged before they no longer were."""
    rows = [
        ("list_vms", "succes"),
        ("get_vm", "succes"),
        ("list_networks", "echec"),  # a failed read stays: it may tell of a problem
        ("create_vm", "succes"),
        ("listen_x", "succes"),  # not a read: no underscore after "list"
    ]
    with database.get_conn() as conn:
        for action, result in rows:
            conn.execute(
                "INSERT INTO audit_log (timestamp, username, action, resource, result) VALUES ('t', 'a', ?, 'r', ?)",
                (action, result),
            )
        conn.commit()
    database.init_db()
    with database.get_conn() as conn:
        left = sorted(r[0] for r in conn.execute("SELECT action FROM audit_log"))
    assert left == ["create_vm", "list_networks", "listen_x"]
