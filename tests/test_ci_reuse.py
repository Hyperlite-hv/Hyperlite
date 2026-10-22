"""scripts/ci-reuse.sh: how much of the CI a commit needs. A release used to run the same suite four times over the same
files (the release PR, its push to test, the test -> master PR, its push to master)."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci-reuse.sh"
CHECKS = (
    "backend",
    "frontend",
    "cli",
    "shell",
    "package",
    "cfs",
    "cfs-cluster (three nodes)",
    "cfs-cluster (two nodes)",
    "e2e (1/4)",
    "e2e",
)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """A repository whose first commit passed every check on a push to test (run 101)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name, text in (("app.py", "print(1)\n"), ("CHANGELOG.md", "# Changelog\n"), ("VERSION", "1.1.0\n")):
        (repo / name).write_text(text)
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "validated")
    return repo


def _commit(repo, name, text):
    (repo / name).write_text(text)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", name)


def _decide(repo, tmp_path, jobs=None, gh_fails=False):
    """mode, from, as the job would get them; `jobs`: the validated run's job conclusions."""
    validated = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    jobs = jobs if jobs is not None else dict.fromkeys(CHECKS, "success")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    lines = "\n".join(f"{k}={v}" for k, v in jobs.items())
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        + ("exit 1\n" if gh_fails else "")
        + 'case "$2" in\n'
        + f'  *workflows/ci.yml/runs*branch=test*) echo "2026-10-21T20:00:00Z 101 {validated}" ;;\n'
        + "  *workflows/ci.yml/runs*) ;;\n"
        + f"  */runs/101/jobs*) printf '%s\\n' '{lines}' ;;\n"
        + f'  */git/commits/*) git -C "{repo}" rev-parse "${{2##*/}}^{{tree}}" ;;\n'
        + "esac\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "output"
    out.unlink(missing_ok=True)
    subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=repo,
        check=True,
        capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REPO": "o/r", "GITHUB_OUTPUT": str(out)},
    )
    values = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return values["mode"], values["from"], validated


def test_the_very_files_that_passed_run_nothing_again(repo, tmp_path):
    """The test -> master PR and the master push of a release: the tree test's tip had."""
    mode, origin, validated = _decide(repo, tmp_path)
    assert (mode, origin) == ("exact", validated)


def test_release_files_alone_skip_only_the_long_jobs(repo, tmp_path):
    _commit(repo, "CHANGELOG.md", "# Changelog\n\n## [1.1.1]\n")
    _commit(repo, "VERSION", "1.1.1\n")
    assert _decide(repo, tmp_path)[0] == "docs"


def test_any_other_change_takes_the_whole_suite(repo, tmp_path):
    _commit(repo, "CHANGELOG.md", "# Changelog\n\n## [1.1.1]\n")
    _commit(repo, "app.py", "print(2)\n")
    assert _decide(repo, tmp_path)[0] == "full"


def test_a_run_with_a_failed_check_validates_nothing(repo, tmp_path):
    jobs = dict.fromkeys(CHECKS, "success") | {"cfs": "failure"}
    assert _decide(repo, tmp_path, jobs)[0] == "full"


def test_skipped_checks_count_only_when_that_run_was_a_reuse(repo, tmp_path):
    skipped = dict.fromkeys(CHECKS, "success") | {"cli": "skipped", "package": "skipped", "cfs": "skipped"}
    assert _decide(repo, tmp_path, skipped)[0] == "full"
    assert _decide(repo, tmp_path, skipped | {"reuse": "success"})[0] == "exact"


def test_a_gate_not_passed_validates_nothing(repo, tmp_path):
    jobs = dict.fromkeys(CHECKS, "success") | {"e2e": "skipped", "reuse": "success"}
    assert _decide(repo, tmp_path, jobs)[0] == "full"


def test_every_part_of_a_matrix_counts(repo, tmp_path):
    """cfs-cluster and the end-to-end parts report as "<job> (<part>)": one failed part validates nothing, and a run
    from before a job existed (no cfs-cluster at all) does not validate these files either."""
    assert _decide(repo, tmp_path)[0] == "exact"
    failed = dict.fromkeys(CHECKS, "success") | {"cfs-cluster (two nodes)": "failure"}
    assert _decide(repo, tmp_path, failed)[0] == "full"
    older = {k: v for k, v in dict.fromkeys(CHECKS, "success").items() if not k.startswith("cfs-cluster")}
    assert _decide(repo, tmp_path, older)[0] == "full"


def test_any_error_runs_everything(repo, tmp_path):
    assert _decide(repo, tmp_path, gh_fails=True)[0] == "full"
