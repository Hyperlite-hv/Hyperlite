"""The release notes the update dialog shows: from CHANGELOG.md for each semver release (and docs/release-notes/fr),
with an index of the versions; none for a dated version; a release without its section stops the publication."""

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _repo(tmp_path, changelog, fr=None):
    (tmp_path / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "release-notes.sh", tmp_path / "scripts")
    (tmp_path / "CHANGELOG.md").write_text(changelog)
    if fr:
        (tmp_path / "docs" / "release-notes" / "fr").mkdir(parents=True)
        for version, text in fr.items():
            (tmp_path / "docs" / "release-notes" / "fr" / f"{version}.md").write_text(text)
    return tmp_path


def _run(repo, version, out):
    return subprocess.run(
        ["bash", str(repo / "scripts" / "release-notes.sh"), version, str(out)], capture_output=True, text=True
    )


CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n- coming\n\n## [1.0.1] - 2026-10-20\n\n- fixed\n\n## [1.0.0] - 2026-10-10\n\n- first\n"


def test_each_release_gets_its_section_and_the_index(tmp_path):
    repo = _repo(tmp_path / "r", CHANGELOG, fr={"1.0.1": "- corrigé\n"})
    out = tmp_path / "notes"
    assert _run(repo, "1.0.0", out).returncode == 0
    assert _run(repo, "1.0.1", out).returncode == 0
    assert (out / "1.0.1.en.md").read_text().strip() == "- fixed"
    assert (out / "1.0.1.fr.md").read_text().strip() == "- corrigé"
    assert (out / "1.0.0.en.md").read_text().strip() == "- first"
    assert json.loads((out / "index.json").read_text()) == [
        {"version": "1.0.1", "fr": True},
        {"version": "1.0.0", "fr": False},
    ]


def test_a_test_build_shows_the_unreleased_section(tmp_path):
    out = tmp_path / "notes"
    assert _run(_repo(tmp_path / "r", CHANGELOG), "1.1.0~test.20261021.0900", out).returncode == 0
    assert (out / "1.1.0~test.20261021.0900.en.md").read_text().strip() == "- coming"


def test_a_dated_version_has_none_and_a_release_without_its_section_stops(tmp_path):
    repo = _repo(tmp_path / "r", CHANGELOG)
    out = tmp_path / "notes"
    assert _run(repo, "2026.10.03.2003", out).returncode == 0 and not out.exists()
    r = _run(repo, "1.2.0", out)
    assert r.returncode == 1 and "no section for 1.2.0" in r.stderr
    assert not (out / "1.2.0.en.md").exists()
