"""What the API reads or writes as www-data from 4e (docs/design/privilege-separation.md, section 14.6): made by root's
services with the API's group, as Proxmox VE's /etc/pve/local files are root:www-data. multi-cluster's run of the lab
under www-data (#298) found each of these refused."""

import os
import stat
from pathlib import Path

import pytest

from app.core import api_group

ROOT = Path(__file__).resolve().parents[1]
AS_ROOT = os.geteuid() == 0 and api_group.gid() is not None


def _group_is_the_apis(path):
    return not AS_ROOT or os.stat(path).st_gid == api_group.gid()


def test_share_gives_the_mode_and_the_apis_group(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    api_group.share(f, 0o640)
    assert stat.S_IMODE(f.stat().st_mode) == 0o640 and _group_is_the_apis(f)


def test_the_database_and_its_journal_are_the_apis_groups_too(database):
    database.init_db()
    for suffix in ("", "-wal", "-shm"):
        path = Path(f"{database.DB_PATH}{suffix}")
        if path.exists():
            assert stat.S_IMODE(path.stat().st_mode) == 0o660, suffix
            assert _group_is_the_apis(path), suffix


def test_a_claim_file_is_the_apis_groups_and_nothing_planted_is_opened(tmp_path, monkeypatch):
    from app.core import paths, shared_claims

    monkeypatch.setattr(paths, "LOCKS", tmp_path / "locks")
    key = shared_claims.take("vm", "local", "web", "a backup")
    try:
        (path,) = (tmp_path / "locks").iterdir()
        assert stat.S_IMODE(path.stat().st_mode) == 0o660 and _group_is_the_apis(path)
        assert stat.S_IMODE((tmp_path / "locks").stat().st_mode) == 0o770
    finally:
        shared_claims.release(key)
    # The API's group writes in the directory: what it could plant there is never followed nor opened by root.
    os.mkfifo(tmp_path / "locks" / "planted.lock")
    with pytest.raises(OSError):
        shared_claims._open(tmp_path / "locks" / "planted.lock", os.O_RDWR | os.O_CREAT)
    (tmp_path / "locks" / "link.lock").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(OSError):
        shared_claims._open(tmp_path / "locks" / "link.lock", os.O_RDWR | os.O_CREAT)
    assert not (tmp_path / "elsewhere").exists()


def test_the_access_log_belongs_to_its_writer_and_the_adm_group(monkeypatch):
    from app.core import access_log

    owners = access_log._owners()
    if os.geteuid() != 0:
        assert owners is None
    elif owners is not None:
        import grp
        import pwd

        assert owners == (pwd.getpwnam("www-data").pw_uid, grp.getgrnam("adm").gr_gid)


def test_the_api_cleans_its_spool_and_the_daemon_roots_staging(tmp_path, monkeypatch):
    """The staging directory is root's alone (0700): the API's start listed it and failed under www-data."""
    from app.core import disk_library

    monkeypatch.setattr(disk_library, "SPOOL", tmp_path / "spool")
    monkeypatch.setattr(disk_library, "STAGING", tmp_path / "staging")
    for d in ("spool", "staging"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "left").write_text("x")
        os.utime(tmp_path / d / "left", (0, 0))
    assert disk_library.clean_spool() == 1
    assert (tmp_path / "staging" / "left").exists()
    assert disk_library.clean_staging() == 1
    assert "disk_library.clean_staging()" in (ROOT / "app" / "daemon" / "__main__.py").read_text()
    assert "clean_staging" not in (ROOT / "app" / "main.py").read_text()


def test_the_package_lays_the_directories_out_for_the_apis_group():
    tmpfiles = (ROOT / "installer" / "hyperlite.tmpfiles").read_text()
    rows = {line.split()[1]: line.split()[2:5] for line in tmpfiles.splitlines() if line and not line.startswith("#")}
    assert rows["/var/lib/hyperlite"] == ["0771", "root", "www-data"]
    assert rows["/var/lib/hyperlite/spool-staging"] == ["0700", "root", "root"]
    assert rows["/run/lock/hyperlite"] == ["0770", "root", "www-data"]
    assert rows["/etc/hyperlite/local/hyperlite.key"] == ["0640", "root", "www-data"]
    assert rows["/var/log/hyperlite"] == ["2750", "www-data", "adm"]
    assert not any("/etc/hyperlite/priv" in p for p in rows)  # root's secrets stay root's alone
    postinst = (ROOT / "installer" / "deb" / "postinst").read_text()
    assert "installer/hyperlite.tmpfiles" in postinst and "systemd-tmpfiles --create" in postinst
    unit = (ROOT / "installer" / "hyperlite.service").read_text()
    pre = [line for line in unit.splitlines() if line.startswith("ExecStartPre=")]
    assert pre and all(line.startswith("ExecStartPre=+") for line in pre)  # root's work, whatever the service's user
