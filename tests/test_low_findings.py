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
