"""The update of the bridge release (1.0.2): the watchdog runs from a copy out of the package and is told the version
to wait for, and its rollback reinstalls the package replaced (docs/design/privilege-separation.md, section 9)."""

import os
import stat
import subprocess
from pathlib import Path

from app.routers import update

WATCHDOG = Path(update.__file__).resolve().parents[2] / "scripts" / "update_watchdog.sh"


def _fake_bin(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _bin(tmp_path, health):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_bin(bin_dir, "curl", health)
    _fake_bin(bin_dir, "sleep", "exit 0")
    _fake_bin(bin_dir, "systemctl", f'echo "$@" >> "{tmp_path}/systemctl.log"')
    _fake_bin(bin_dir, "apt-get", f'echo "$HYPERLITE_SKIP_RESTART $@" >> "{tmp_path}/apt.log"')
    return bin_dir


def test_the_new_version_is_awaited_even_when_the_package_no_longer_ships_version(tmp_path):
    repo = tmp_path / "gone"  # 1.1 moved the code: no <repo>/VERSION any more
    bin_dir = _bin(tmp_path, """echo '{"status":"ok","hyperlite_version":"1.1.0"}'""")
    subprocess.run(
        ["bash", str(WATCHDOG), str(tmp_path / "none.tar.gz"), str(repo), str(tmp_path / "update.log")],
        check=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HYPERLITE_EXPECTED_VERSION": "1.1.0"},
    )
    assert "update validated" in (tmp_path / "update.log").read_text()


def test_a_failed_update_reinstalls_the_package_replaced_instead_of_extracting_the_archive(tmp_path):
    repo = tmp_path / "hyperlite"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "main.py").write_text("the new version's code\n")
    tarball = tmp_path / "backup.tar.gz"
    subprocess.run(["tar", "czf", str(tarball), "-C", str(tmp_path), "hyperlite"], check=True)
    deb = tmp_path / "hyperlite_1.0.2_amd64.deb"
    deb.write_text("deb")
    bin_dir = _bin(tmp_path, "exit 7")  # the new version never answers
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HYPERLITE_EXPECTED_VERSION": "1.1.0",
           "HYPERLITE_PREVIOUS_DEB": str(deb)}  # fmt: skip
    subprocess.run(["bash", str(WATCHDOG), str(tarball), str(repo), str(tmp_path / "update.log")], check=True, env=env)

    assert (tmp_path / "apt.log").read_text().split() == ["1", "install", "-y", "--allow-downgrades", str(deb)]
    assert (tmp_path / "systemctl.log").read_text().splitlines() == ["stop hyperlite", "start hyperlite"]
    assert "reinstalled" in (tmp_path / "update.log").read_text()


def test_the_updater_starts_a_staged_copy_told_the_version_and_the_package(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "WATCHDOG_STAGE", tmp_path / "update" / "update_watchdog.sh")
    staged = update._stage_watchdog()
    assert staged.read_bytes() == WATCHDOG.read_bytes()
    assert staged.stat().st_mode & 0o777 == 0o700 and staged.parent.stat().st_mode & 0o777 == 0o700
    argv = update._watchdog_argv("/b.tar.gz", "/u.log", "1:1.1.0", {"file": Path("/k/hyperlite_1.0.2_amd64.deb")})
    assert argv[:3] == [
        "env",
        "HYPERLITE_EXPECTED_VERSION=1.1.0",
        "HYPERLITE_PREVIOUS_DEB=/k/hyperlite_1.0.2_amd64.deb",
    ]
    assert argv[3:5] == ["bash", str(staged)]
    assert "HYPERLITE_PREVIOUS_DEB" not in " ".join(update._watchdog_argv("/b", "/u", "1.0.2", None))
