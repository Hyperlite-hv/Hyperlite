from app.routers import update


def test_only_the_newest_update_backups_are_kept(tmp_path):
    names = [f"hyperlite-backup-2026090{i}T000000Z.tar.gz" for i in range(1, 7)]
    for name in names:
        (tmp_path / name).write_text("x")
    (tmp_path / "pre-apt-adoption-20260917T165612Z.tar.gz").write_text("keep me")
    (tmp_path / "update.log").write_text("log")

    update._prune_old_backups(tmp_path, keep=3)

    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == sorted([*names[-3:], "pre-apt-adoption-20260917T165612Z.tar.gz", "update.log"])


def _layout(tmp_path, monkeypatch):
    """The code and /etc/hyperlite of a node, under tmp_path; the archive rooted there instead of /."""
    from app.core import paths

    code, etc = tmp_path / "usr/lib/hyperlite", tmp_path / "etc/hyperlite"
    for path in (
        code / "app/main.py",
        code / "venv/bin/python3",
        code / "app/__pycache__/main.pyc",
        etc / "priv/env",
        etc / "priv/ssh/hyperlite_cluster",
        etc / "local/hyperlite.key",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    monkeypatch.setattr(update, "ARCHIVE_ROOT", tmp_path)
    monkeypatch.setattr(update, "REPO_DIR", code)
    monkeypatch.setattr(paths, "ETC", etc)
    return code, etc


def test_the_archive_holds_the_code_and_the_nodes_secrets_not_what_is_derived(tmp_path, monkeypatch):
    """The data (/var/lib/hyperlite: ISO images, VM backups, the database) is never in it: an update does not change
    it, and the database is copied separately and consistently (SQLite backup API)."""
    import subprocess

    _layout(tmp_path, monkeypatch)
    tarball = tmp_path / "b.tar.gz"
    cmd = ["tar", "czf", str(tarball), *update._BACKUP_EXCLUDES, "-C", str(tmp_path), *update._backup_members()]
    subprocess.run(cmd, check=True)
    listing = subprocess.run(["tar", "-tzf", str(tarball)], capture_output=True, text=True, check=True).stdout
    for kept in (
        "usr/lib/hyperlite/app/main.py",
        "etc/hyperlite/priv/env",
        "etc/hyperlite/priv/ssh/hyperlite_cluster",
        "etc/hyperlite/local/hyperlite.key",
    ):
        assert kept in listing, kept
    assert "venv" not in listing and "__pycache__" not in listing


def test_update_backups_are_readable_by_root_only(tmp_path, monkeypatch):
    import sqlite3
    import stat
    import subprocess

    _layout(tmp_path, monkeypatch)
    db = tmp_path / "hyperlite.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (42)")
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o755)
    (backups / "hyperlite-backup-20260101T000000Z.tar.gz").write_text("old archive")
    (backups / "hyperlite-backup-20260101T000000Z.tar.gz").chmod(0o644)
    monkeypatch.setattr(update, "BACKUP_DIR", backups)
    monkeypatch.setattr(update, "DB_PATH", db)
    access = tmp_path / "etc/hyperlite/access.db"
    with sqlite3.connect(access) as conn:
        conn.execute("CREATE TABLE users (username)")
        conn.execute("INSERT INTO users VALUES ('ana')")
    monkeypatch.setattr(update, "access_path", lambda: access)

    tarball = update._backup("t")

    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    for path in backups.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    copy = tarball.with_name(tarball.name.replace(".tar.gz", ".db"))
    with sqlite3.connect(copy) as conn:
        assert conn.execute("SELECT x FROM t").fetchone()[0] == 42
    with sqlite3.connect(
        backups / tarball.name.replace("hyperlite-backup-", "hyperlite-access-backup-").replace(".tar.gz", ".db")
    ) as conn:
        assert conn.execute("SELECT username FROM users").fetchone()[0] == "ana"
    listing = subprocess.run(["tar", "-tzf", str(tarball)], capture_output=True, text=True, check=True).stdout
    assert "etc/hyperlite/priv/env" in listing and "hyperlite.db" not in listing and "access.db" not in listing
