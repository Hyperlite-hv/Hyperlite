"""The rollback watchdog must restore the backup made by app.routers.update."""

import os
import stat
import subprocess
from pathlib import Path

from app import main as app_main
from app.routers import update

WATCHDOG = Path(update.__file__).resolve().parents[2] / "scripts" / "update_watchdog.sh"


def _fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_watchdog_restores_the_backup_over_the_real_files(tmp_path):
    repo = tmp_path / "hyperlite"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("GOOD = True\n")

    # Same command as update._backup: the archive is rooted at the directory name.
    tarball = tmp_path / "backup.tar.gz"
    subprocess.run(["tar", "czf", str(tarball), "-C", str(repo.parent), repo.name], check=True)
    (repo / "app" / "main.py").write_text("raise RuntimeError('broken release')\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_bin(bin_dir, "curl", "exit 7")  # the new code never answers on /health
    _fake_bin(bin_dir, "sleep", "exit 0")
    _fake_bin(bin_dir, "systemctl", f'echo "$@" >> "{tmp_path}/systemctl.log"')
    log_file = tmp_path / "update.log"

    subprocess.run(
        ["bash", str(WATCHDOG), str(tarball), str(repo), str(log_file)],
        check=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert (repo / "app" / "main.py").read_text() == "GOOD = True\n"
    assert not (repo / "hyperlite").exists(), "the archive must not be nested inside the repository"
    assert "restart hyperlite" in (tmp_path / "systemctl.log").read_text()
    assert "ROLLBACK" in log_file.read_text()


def test_watchdog_leaves_a_healthy_update_alone(tmp_path):
    repo = tmp_path / "hyperlite"
    repo.mkdir()
    (repo / "VERSION").write_text("new\n")
    tarball = tmp_path / "backup.tar.gz"
    subprocess.run(["tar", "czf", str(tarball), "-C", str(repo.parent), repo.name], check=True)
    (repo / "VERSION").write_text("newer\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_bin(bin_dir, "curl", "exit 0")
    _fake_bin(bin_dir, "sleep", "exit 0")
    _fake_bin(bin_dir, "systemctl", "exit 1")
    log_file = tmp_path / "update.log"

    subprocess.run(
        ["bash", str(WATCHDOG), str(tarball), str(repo), str(log_file)],
        check=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert (repo / "VERSION").read_text() == "newer\n"
    assert "update validated" in log_file.read_text()


def test_running_version_comes_from_the_version_file():
    version = (Path(app_main.__file__).resolve().parent.parent / "VERSION").read_text().strip()
    assert app_main._running_version() == version
