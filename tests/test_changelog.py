"""The changelog's fragments (scripts/changelog.py): the repository's are well formed, the unreleased changes read as
the version's section will, entries left in CHANGELOG.md are kept, and a release writes the section and removes them."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "changelog.py"

spec = importlib.util.spec_from_file_location("changelog", SCRIPT)
changelog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(changelog)

HEAD = "# Changelog\n\nIntro.\n\n## [Unreleased]\n"
RELEASED = "## [1.1.3] - 2026-10-22\n\n### Fixed\n\n- an old fix\n"


def _repo(tmp_path, unreleased="\n", fragments=None):
    (tmp_path / "CHANGELOG.md").write_text(f"{HEAD}{unreleased}{RELEASED}")
    folder = tmp_path / "changelog.d"
    folder.mkdir()
    shutil.copy(ROOT / "changelog.d" / "README.md", folder)
    for name, text in (fragments or {}).items():
        (folder / name).write_text(text)
    return tmp_path


def _run(root, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--root", str(root)], capture_output=True, text=True, check=False
    )


def test_the_repository_fragments_are_well_formed():
    assert changelog.problems() == []


def test_fragments_read_by_type_in_keep_a_changelog_order_and_by_issue(tmp_path):
    root = _repo(
        tmp_path,
        fragments={
            "440-db.fixed.md": "- the database fix\n",
            "99-early.fixed.md": "- an earlier issue's fix\n",
            "512-net.added.md": "- bridges\n\n- bonds\n",
            "511-daemon.security.md": "- the daemon\n",
        },
    )
    assert changelog.unreleased(root) == (
        "### Added\n\n- bridges\n\n- bonds\n\n"
        "### Fixed\n\n- an earlier issue's fix\n\n- the database fix\n\n"
        "### Security\n\n- the daemon"
    )


def test_entries_left_in_the_changelog_come_first_in_their_type(tmp_path):
    root = _repo(
        tmp_path,
        unreleased="\n- untyped, from before\n\n### Fixed\n\n- written in CHANGELOG.md\n\n### Notes\n\n- a note\n\n",
        fragments={"440-db.fixed.md": "- a fragment\n", "441-csv.added.md": "- CSV\n"},
    )
    assert changelog.unreleased(root) == (
        "- untyped, from before\n\n### Notes\n\n- a note\n\n"
        "### Added\n\n- CSV\n\n"
        "### Fixed\n\n- written in CHANGELOG.md\n\n- a fragment"
    )


@pytest.mark.parametrize(
    ("name", "text", "said"),
    [
        ("440-db.md", "- x\n", "<issue>-<slug>.<type>.md"),
        ("440-db.bugfix.md", "- x\n", "<issue>-<slug>.<type>.md"),
        ("440-db.fixed.md", "A sentence.\n", "starting with '- '"),
        ("440-db.fixed.md", "", "starting with '- '"),
        ("440-db.fixed.md", "- x\n\n### Fixed\n", "no heading"),
    ],
)
def test_a_malformed_fragment_is_refused(tmp_path, name, text, said):
    root = _repo(tmp_path, fragments={name: text})
    result = _run(root, "check")
    assert result.returncode == 1 and said in result.stdout
    assert _run(root, "unreleased").returncode != 0, "nothing is assembled from a malformed fragment"


def test_a_release_writes_the_section_and_removes_the_fragments(tmp_path):
    root = _repo(
        tmp_path,
        unreleased="\n### Security\n\n- from CHANGELOG.md\n\n",
        fragments={"440-db.added.md": "- the database\n", "440-claim.fixed.md": "- claim.py\n"},
    )
    result = _run(root, "release", "1.1.4", "--date", "2026-10-23")
    assert result.returncode == 0, result.stderr
    assert (root / "CHANGELOG.md").read_text() == (
        f"{HEAD}\n## [1.1.4] - 2026-10-23\n\n"
        "### Added\n\n- the database\n\n### Fixed\n\n- claim.py\n\n### Security\n\n- from CHANGELOG.md\n\n"
        f"{RELEASED}"
    )
    assert sorted(p.name for p in (root / "changelog.d").iterdir()) == ["README.md"]
    assert _run(root, "unreleased").stdout == "", "nothing left unreleased"


def test_a_release_refuses_a_version_already_there_or_nothing_to_release(tmp_path):
    root = _repo(tmp_path, fragments={"440-db.added.md": "- the database\n"})
    again = _run(root, "release", "1.1.3")
    assert again.returncode != 0 and "already has a section for 1.1.3" in again.stderr
    assert (root / "changelog.d" / "440-db.added.md").exists()
    assert _run(root, "release", "v1.1.4").returncode == 2

    (tmp_path / "empty").mkdir()
    empty = _repo(tmp_path / "empty")
    nothing = _run(empty, "release", "1.1.4")
    assert nothing.returncode != 0 and "Nothing to release" in nothing.stderr
