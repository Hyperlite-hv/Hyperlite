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


def test_bulky_user_data_is_left_out_of_the_backup():
    for path in ("data/isos", "data/backups", "data/vm-exports", "data/templates", "data/imported-disks"):
        assert f"--exclude={path}" in update._BACKUP_EXCLUDES
    assert "--exclude=data" not in update._BACKUP_EXCLUDES


def test_tar_excludes_bulky_data_but_keeps_the_rest(tmp_path):
    import subprocess

    repo = tmp_path / "hyperlite"
    for rel in (
        "data/isos/big.iso",
        "data/backups/vm/disk.qcow2",
        "data/ssh/key",
        "app/main.py",
        ".env",
        "hyperlite.db",
    ):
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    tarball = tmp_path / "b.tar.gz"
    subprocess.run(["tar", "czf", str(tarball), *update._BACKUP_EXCLUDES, "-C", str(tmp_path), "hyperlite"], check=True)
    listing = subprocess.run(["tar", "-tzf", str(tarball)], capture_output=True, text=True, check=True).stdout
    assert "hyperlite/data/isos/big.iso" not in listing
    assert "hyperlite/data/backups/vm/disk.qcow2" not in listing
    for kept in ("hyperlite/data/ssh/key", "hyperlite/app/main.py", "hyperlite/.env", "hyperlite/hyperlite.db"):
        assert kept in listing
