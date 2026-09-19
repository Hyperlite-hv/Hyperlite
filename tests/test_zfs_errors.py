"""ZFS storage helpers that do not need ZFS installed."""

import subprocess

import pytest

from app.core import zfs_storage


def test_zfs_is_reported_unavailable_when_the_binaries_are_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert zfs_storage.is_available() is False


def _raise_file_not_found(*args, **kwargs):
    raise FileNotFoundError("zpool")


def test_missing_binaries_raise_a_clean_error_instead_of_a_raw_exception(monkeypatch):
    """Regression: a raw FileNotFoundError used to break the whole GET /storage endpoint."""
    monkeypatch.setattr(subprocess, "run", _raise_file_not_found)
    with pytest.raises(zfs_storage.ZfsError):
        zfs_storage._run("zpool", "list")


def test_missing_binaries_can_be_tolerated_by_callers_that_do_not_check(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _raise_file_not_found)
    result = zfs_storage._run("zpool", "list", check=False)
    assert result.returncode == 127


def test_not_found_is_a_distinct_error_type_that_callers_can_map_to_404():
    assert issubclass(zfs_storage.ZfsNotFoundError, zfs_storage.ZfsError)
    with pytest.raises(zfs_storage.ZfsError):
        raise zfs_storage.ZfsNotFoundError("ZFS pool 'x' not found")
