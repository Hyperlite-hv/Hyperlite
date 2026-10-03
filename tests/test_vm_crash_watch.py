"""A VM of this host that stops by accident is audited as vm_crashed (a notification event); a stop, a migration or
a failed start is not."""

import libvirt
import pytest

from app.core import vm_crash_watch

RUN = (libvirt.VIR_DOMAIN_RUNNING, 1)
OFF = (libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_SHUTOFF_SHUTDOWN)
DESTROYED = (libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_SHUTOFF_DESTROYED)
MIGRATED = (libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_SHUTOFF_MIGRATED)
QEMU_DIED = (libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_SHUTOFF_CRASHED)
FAILED = (libvirt.VIR_DOMAIN_SHUTOFF, libvirt.VIR_DOMAIN_SHUTOFF_FAILED)
PANIC = (libvirt.VIR_DOMAIN_CRASHED, libvirt.VIR_DOMAIN_CRASHED_PANICKED)


class _Domain:
    def __init__(self, name, state):
        self._name, self.now = name, state

    def UUIDString(self):
        return f"uuid-{self._name}"

    def name(self):
        return self._name

    def state(self):
        return self.now


class _Conn:
    def __init__(self, *domains):
        self.domains = list(domains)

    def listAllDomains(self):
        return self.domains


@pytest.fixture()
def watch(database, monkeypatch):
    from app.core import audit

    logged = []
    monkeypatch.setattr(audit, "log_action", lambda *a, **k: logged.append(a))
    vm_crash_watch.forget()
    yield logged
    vm_crash_watch.forget()


@pytest.mark.parametrize(("before", "after", "crashed"), [
    (RUN, QEMU_DIED, True),
    (RUN, PANIC, True),
    (RUN, FAILED, True),
    (OFF, FAILED, False),  # a start that failed: answered to whoever started it
    (RUN, OFF, False),
    (RUN, DESTROYED, False),
    (RUN, MIGRATED, False),
])  # fmt: skip
def test_only_an_accident_is_a_crash(watch, before, after, crashed):
    vm = _Domain("web", before)
    conn = _Conn(vm)
    assert vm_crash_watch.tick(conn) == []  # the first round only records
    vm.now = after
    assert bool(vm_crash_watch.tick(conn)) is crashed
    assert [a[1:3] for a in watch] == ([("vm_crashed", "web")] if crashed else [])
    assert vm_crash_watch.tick(conn) == []  # told once


def test_a_vm_found_crashed_at_start_is_not_told_again(watch):
    conn = _Conn(_Domain("old", QEMU_DIED))
    assert vm_crash_watch.tick(conn) == [] and vm_crash_watch.tick(conn) == []
    assert watch == []


def test_the_crash_is_a_notification_event():
    from app.core.notifications import NOTIFY_EVENTS

    assert "vm_crashed" in NOTIFY_EVENTS
