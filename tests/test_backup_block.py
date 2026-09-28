"""Backups and exports copy disk files only: a VM with a ZFS zvol or an iSCSI LUN is refused with a clear message,
instead of an unclear "No disk found" (all disks are block devices) or a backup that silently leaves the block disk
out (a VM mixing both)."""

import pytest
from fastapi import HTTPException

from app.core import backups


class _Domain:
    def __init__(self, *disks):
        parts = []
        for kind, dev in disks:
            source = f"<source dev='/dev/zvol/tank/{dev}'/>" if kind == "block" else f"<source file='/x/{dev}.qcow2'/>"
            parts.append(f"<disk type='{kind}' device='disk'>{source}<target dev='{dev}'/></disk>")
        parts.append("<disk type='file' device='cdrom'><target dev='sdz'/></disk>")
        self._xml = f"<domain><devices>{''.join(parts)}</devices></domain>"

    def XMLDesc(self, *_):
        return self._xml


def test_block_disks_are_found_and_cdroms_ignored():
    assert backups.block_disks(_Domain(("block", "sda"), ("file", "sdb"))) == ["sda"]
    assert backups.block_disks(_Domain(("file", "sda"))) == []


def test_a_vm_mixing_a_zvol_and_a_file_is_refused():
    error = backups.block_disk_error(_Domain(("file", "sda"), ("block", "sdb")), "A backup")
    assert error and "sdb" in error and "ZFS or iSCSI" in error
    assert backups.block_disk_error(_Domain(("file", "sda")), "A backup") is None


def test_the_endpoint_refusal_is_a_422(monkeypatch):
    class _Conn:
        def lookupByName(self, name):
            return _Domain(("block", "sda"))

        def close(self):
            pass

    monkeypatch.setattr(backups, "open_conn", lambda *a, **k: _Conn())
    with pytest.raises(HTTPException) as err:
        backups.refuse_vm_with_block_disks("vm1", "An export")
    assert err.value.status_code == 422 and err.value.detail.startswith("An export is not available yet")
