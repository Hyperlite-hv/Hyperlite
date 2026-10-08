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
    monkeypatch.setattr(update.version, "STARTUP_VERSION", "2026.09.20.1249")
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
    monkeypatch.setattr(update.version, "STARTUP_VERSION", "2026.09.27.1838")
    result = update._check_update_apt()
    assert result["verifiable"] is True and result["a_jour"] is True
    assert result["rolled_back"] is False


def test_a_rolled_back_update_is_not_up_to_date(apt, monkeypatch):
    # The real case: 2026.09.28.1013 was installed, did not answer in time and was rolled back; dpkg still says
    # 1013, the service runs 0743. The check used to answer "up to date" and the update could not be re-applied.
    apt(_policy("2026.09.28.1013", "2026.09.28.1013", (500, f"{OFFICIAL} stable/main amd64 Packages")))
    monkeypatch.setattr(update, "_dpkg_installed_version", lambda: "2026.09.28.1013")
    monkeypatch.setattr(update.version, "STARTUP_VERSION", "2026.09.28.0743")
    result = update._check_update_apt()
    assert result["verifiable"] is True and result["a_jour"] is False and result["rolled_back"] is True
    assert result["commit_local"] == "2026.09.28.0743" and result["commit_distant"] == "2026.09.28.1013"
    assert "rolled back" in result["changelog"][0]


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


def test_the_epoch_of_a_semver_release_is_never_shown(apt):
    """From 1.0.0 the package is 1:1.0.0 (or apt would order it below the dated versions); /health says 1.0.0."""
    apt(_policy("2026.10.03.2003", "1:1.0.0", (500, f"{OFFICIAL} stable/main amd64 Packages")))
    result = update._check_update_apt()
    assert result["commit_distant"] == "1.0.0" and result["a_jour"] is False


def test_the_notes_between_the_installed_version_and_the_candidate(monkeypatch, tmp_path):
    conf = tmp_path / "apt-source.conf"
    conf.write_text(f'HYPERLITE_APT_URL="{OFFICIAL}"\n')
    monkeypatch.setattr(update, "APT_SOURCE_CONF", conf)
    served = {
        f"{OFFICIAL}/notes/index.json": '[{"version": "1.2.0", "fr": true}, {"version": "1.1.0", "fr": false},'
        ' {"version": "1.0.0", "fr": true}]',
        f"{OFFICIAL}/notes/1.2.0.fr.md": "nouveautés 1.2",
        f"{OFFICIAL}/notes/1.1.0.en.md": "what is new in 1.1",
        f"{OFFICIAL}/notes/1.0.0.en.md": "first release",
    }

    def fetch(url):
        if url not in served:
            raise OSError("404")
        return served[url]

    monkeypatch.setattr(update, "_fetch_text", fetch)
    notes = update._release_notes("1.0.0", "1.2.0", "fr")
    assert [(n["version"], n["langue"], n["texte"]) for n in notes] == [
        ("1.2.0", "fr", "nouveautés 1.2"),
        ("1.1.0", "en", "what is new in 1.1"),  # no French notes: the English ones
    ]
    # From a dated version every semver release is newer; a mirror that does not answer leaves the notes out.
    assert [n["version"] for n in update._release_notes("2026.10.03.2003", "1.1.0", "en")] == ["1.1.0", "1.0.0"]
    monkeypatch.setattr(update, "_fetch_text", lambda url: (_ for _ in ()).throw(OSError("offline")))
    assert update._release_notes("1.0.0", "1.2.0", "en") == []
