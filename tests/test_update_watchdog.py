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
    _fake_bin(bin_dir, "curl", """echo '{"status":"ok","hyperlite_version":"newer"}'""")
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


def _run_with_health(tmp_path, answers):
    """Run the watchdog after an update to "new" while /health answers, attempt after attempt, with the given
    versions (the last one repeats). Returns (log text, systemctl calls)."""
    repo = tmp_path / "hyperlite"
    repo.mkdir()
    (repo / "VERSION").write_text("old\n")
    tarball = tmp_path / "backup.tar.gz"
    subprocess.run(["tar", "czf", str(tarball), "-C", str(repo.parent), repo.name], check=True)
    (repo / "VERSION").write_text("new\n")
    (tmp_path / "answers").write_text("\n".join(answers) + "\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_bin(
        bin_dir,
        "curl",
        f"""n=$(cat "{tmp_path}/count" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{tmp_path}/count"
v=$(sed -n "${{n}}p" "{tmp_path}/answers"); [ -n "$v" ] || v=$(tail -n 1 "{tmp_path}/answers")
[ "$v" = down ] && exit 7
printf '{{"status":"ok","hyperlite_version":"%s"}}\\n' "$v"
""",
    )
    _fake_bin(bin_dir, "sleep", "exit 0")
    _fake_bin(bin_dir, "systemctl", f'echo "$@" >> "{tmp_path}/systemctl.log"')
    log_file = tmp_path / "update.log"
    subprocess.run(
        ["bash", str(WATCHDOG), str(tarball), str(repo), str(log_file)],
        check=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    calls = (tmp_path / "systemctl.log").read_text() if (tmp_path / "systemctl.log").exists() else ""
    return log_file.read_text(), calls


def test_watchdog_waits_for_a_slow_shutdown_instead_of_rolling_back(tmp_path):
    # The real case: the previous process kept answering for ~30 s while it closed its connections, then the
    # new one started. The old watchdog gave up after ~33 s and rolled a good update back.
    log, calls = _run_with_health(tmp_path, ["old"] * 9 + ["down"] * 3 + ["new"])
    assert "update validated" in log and "ROLLBACK" not in log
    assert calls == ""
    assert (tmp_path / "hyperlite" / "VERSION").read_text() == "new\n"


def test_watchdog_rolls_back_when_only_the_previous_process_ever_answers(tmp_path):
    log, calls = _run_with_health(tmp_path, ["old"])
    assert "ROLLBACK" in log
    assert "restart hyperlite" in calls
    assert (tmp_path / "hyperlite" / "VERSION").read_text() == "old\n"


def test_health_reports_the_version_the_process_started_with(tmp_path, monkeypatch):
    from app.core import version

    started = version.STARTUP_VERSION
    fake = tmp_path / "VERSION"
    fake.write_text("installed-but-not-running\n")
    monkeypatch.setattr(version, "VERSION_FILE", fake)
    assert version.read_version_file() == "installed-but-not-running"
    assert app_main._running_version() == started
