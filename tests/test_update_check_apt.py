"""The APT update check must never answer "up to date" when it cannot see the Hyperlite repository (a real
case: a machine stuck on an old release whose source line had been commented out)."""

import subprocess

import pytest

from app.routers import update

OFFICIAL = "https://hyperlite-hv.github.io"


def _policy(installed, candidate, *sources):
    table = "\n".join(f"        {prio} {src}" for prio, src in sources)
    return f"hyperlite:\n  Installed: {installed}\n  Candidate: {candidate}\n  Version table:\n *** {installed} 100\n{table}\n"


@pytest.fixture()
def apt(monkeypatch, tmp_path):
    conf = tmp_path / "apt-source.conf"
    conf.write_text(f'# comment\nHYPERLITE_APT_URL="{OFFICIAL}"\n')
    monkeypatch.setattr(update, "APT_SOURCE_CONF", conf)
    monkeypatch.setattr(update, "_dpkg_installed_version", lambda: "2026.09.20.1249")
    monkeypatch.setattr(update, "_apt_update_with_retry", lambda **_: subprocess.CompletedProcess([], 0, "", ""))

    def set_policy(text):
        monkeypatch.setattr(update, "_run_c", lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, text, ""))

    return set_policy


def test_repository_missing_is_not_up_to_date(apt):
    apt(_policy("2026.09.20.1249", "2026.09.20.1249", (100, "/var/lib/dpkg/status")))
    result = update._check_update_apt()
    assert result["verifiable"] is False
    assert "not among this machine's APT sources" in result["erreur"]
    assert f"{OFFICIAL} stable main" in result["erreur"]


def test_old_repository_address_is_reported(apt):
    apt(
        _policy(
            "2026.09.20.1249",
            "2026.09.20.1249",
            (500, "https://twikles.github.io/hyperlite stable/main amd64 Packages"),
            (100, "/var/lib/dpkg/status"),
        )
    )
    result = update._check_update_apt()
    assert result["verifiable"] is False
    assert "no longer the official address" in result["erreur"]


def test_official_repository_with_newer_version(apt):
    apt(
        _policy(
            "2026.09.20.1249",
            "2026.09.27.1838",
            (500, f"{OFFICIAL} stable/main amd64 Packages"),
            (100, "/var/lib/dpkg/status"),
        )
    )
    result = update._check_update_apt()
    assert result["verifiable"] is True and result["a_jour"] is False
    assert result["commit_distant"] == "2026.09.27.1838"


def test_official_repository_up_to_date(apt, monkeypatch):
    apt(
        _policy(
            "2026.09.27.1838",
            "2026.09.27.1838",
            (500, f"{OFFICIAL}/ stable/main amd64 Packages"),
            (100, "/var/lib/dpkg/status"),
        )
    )
    monkeypatch.setattr(update, "_dpkg_installed_version", lambda: "2026.09.27.1838")
    result = update._check_update_apt()
    assert result["verifiable"] is True and result["a_jour"] is True


def test_hourly_check_alerts_once_when_the_source_is_missing(apt, database, monkeypatch):
    from app.core import audit, update_check

    apt(_policy("2026.09.20.1249", "2026.09.20.1249", (100, "/var/lib/dpkg/status")))
    monkeypatch.setattr(update, "_install_method", lambda: "apt")
    update_check.check_once()
    update_check.check_once()
    audit._AUDIT_QUEUE.join()
    with database.get_conn() as conn:
        rows = conn.execute("SELECT * FROM audit_log WHERE action = 'update_check_blocked'").fetchall()
    assert len(rows) == 1
