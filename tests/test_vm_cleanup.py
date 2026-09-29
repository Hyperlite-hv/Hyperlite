"""Automatic deletion of inactive VMs: never without the promised warning, never a running, HA-protected or busy
VM, and one VM's failure never stops the cycle for the others."""

from datetime import UTC, datetime, timedelta

import libvirt
import pytest

from app.core import vm_cleanup, vm_locks


class Domain:
    def __init__(self, name, active=False):
        self._name = name
        self.active = active

    def isActive(self):
        return self.active


class Conn:
    def __init__(self, domains):
        self.domains = domains

    def lookupByName(self, name):
        if name not in self.domains:
            raise libvirt.libvirtError("no domain")
        return self.domains[name]

    def close(self):
        pass


def _ago(**kw):
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


@pytest.fixture()
def cycle(database, monkeypatch):
    deleted = []
    domains = {}
    monkeypatch.setattr(vm_cleanup, "open_conn", lambda *a, **k: Conn(domains))
    import app.routers.vms.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_perform_vm_deletion", lambda conn, domain, name, node=None: deleted.append(name))

    def add(name, days=7, last_active=None, warned=None, active=False):
        domains[name] = Domain(name, active)
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO vm_auto_cleanup (vm_name, inactive_days, last_active_at, warned_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, days, last_active or _ago(days=days + 1), warned, _ago(days=30)),
            )
            conn.commit()

    def warned_at(name):
        with database.get_conn() as conn:
            return conn.execute("SELECT warned_at FROM vm_auto_cleanup WHERE vm_name = ?", (name,)).fetchone()[0]

    return add, deleted, warned_at


def test_a_vm_past_its_threshold_but_never_warned_is_warned_not_deleted(cycle):
    add, deleted, warned_at = cycle
    add("lab1", days=7, last_active=_ago(days=30))  # the service was down during the whole warning window
    vm_cleanup.check_once()
    assert deleted == []
    assert warned_at("lab1") is not None


def test_a_vm_is_deleted_only_once_the_warning_has_been_out_long_enough(cycle):
    add, deleted, _ = cycle
    add("recent", days=7, last_active=_ago(days=8), warned=_ago(hours=2))
    add("due", days=7, last_active=_ago(days=8), warned=_ago(hours=25))
    vm_cleanup.check_once()
    assert deleted == ["due"]


def test_running_or_busy_vms_are_left_alone(cycle):
    add, deleted, _ = cycle
    add("running", last_active=_ago(days=30), warned=_ago(days=2), active=True)
    add("busy", last_active=_ago(days=30), warned=_ago(days=2))
    held = vm_locks.claim("busy", "a backup")
    try:
        vm_cleanup.check_once()
    finally:
        held.release()
    assert deleted == []


def test_one_vm_failing_does_not_stop_the_cycle(cycle, monkeypatch):
    add, deleted, _ = cycle
    add("broken", last_active="not a date", warned=_ago(days=2))
    add("due", last_active=_ago(days=30), warned=_ago(days=2))
    vm_cleanup.check_once()
    assert deleted == ["due"]


def test_an_orphaned_entry_is_removed(cycle, database):
    add, _, _ = cycle
    add("gone")
    vm_cleanup._check_vm(Conn({}), {"vm_name": "gone"})
    with database.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM vm_auto_cleanup WHERE vm_name = 'gone'").fetchone()[0] == 0
