"""Small findings of the code review: a VM gone during a metrics tick, notification channels unreadable."""

import libvirt

from app.core import metrics, notifications


def test_a_vm_gone_since_the_listing_is_skipped_not_the_whole_tick():
    class Gone:
        def isActive(self):
            raise libvirt.libvirtError("domain not found")

    assert metrics._sample_vm(Gone(), "local:web", 0.0) is None


def test_unreadable_channels_are_logged_and_never_raised(monkeypatch, caplog):
    def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(notifications, "list_channels", broken)
    event = next(iter(notifications.NOTIFY_EVENTS))
    notifications.notify(event, "t", "m")
    assert "channels could not be read" in caplog.text


def test_rebuilding_an_empty_table_keeps_its_autoincrement_counter(monkeypatch):
    import sqlite3

    from app.core import schema_v1

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE things (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT)")
    conn.execute("INSERT INTO things (kind) VALUES ('a'), ('b'), ('c')")
    conn.execute("DELETE FROM things")  # empty, but ids 1 to 3 were given out
    monkeypatch.setattr(schema_v1, "_value_renames", lambda table: {})
    schema_v1._rebuild(conn, "things")
    conn.execute("INSERT INTO things (kind) VALUES ('d')")
    assert conn.execute("SELECT id FROM things").fetchone()[0] == 4


def test_a_cluster_whose_record_fails_is_not_left_being_created(monkeypatch):
    import pytest

    from app.core import k8s_cluster

    monkeypatch.setattr(k8s_cluster, "precheck", lambda *a: None)
    monkeypatch.setattr(k8s_cluster, "create_task", lambda *a, **k: "t")

    class Store:
        def create_k8s_cluster(self, *a):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(k8s_cluster, "_store", lambda: Store())
    with pytest.raises(RuntimeError):
        k8s_cluster.start_create("lab", 1, 2, 2048, 20, "default", "admin")
    assert "lab" not in k8s_cluster._busy


def test_an_iso_copy_that_cannot_start_leaves_no_marker(monkeypatch):
    import pytest

    from app.core import iso_share

    monkeypatch.setattr(iso_share, "resolve_node", lambda key: None if key == iso_share.LOCAL else {"name": key})
    monkeypatch.setattr(iso_share, "iso_size", lambda node, name: 10 if node is None else None)

    def no_task(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(iso_share, "create_task", no_task)
    with pytest.raises(RuntimeError):
        iso_share.start_copy("debian.iso", None, ["n2", "n3"], "admin")
    assert not {k for k in iso_share._active if k[0] == "debian.iso"}
