"""scripts/ci-scope.sh: which long jobs of the CI a pull request needs, from the files it changes. Every pull request
ran every job (about eight minutes), a change to the documentation or to the client included."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci-scope.sh"
ALL = {"e2e": "true", "cfs": "true", "cli": "true", "package": "true"}


def _scope(tmp_path, files, event="pull_request", gh_fails=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    listing = tmp_path / "files.txt"
    listing.write_text("".join(f"{f}\n" for f in files))
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/bash\n" + ("exit 1\n" if gh_fails else "") + f'cat "{listing}"\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "output"
    out.unlink(missing_ok=True)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "REPO": "o/r", "GITHUB_OUTPUT": str(out)}
    env |= {"EVENT": event, "PR": "12"}
    subprocess.run(["bash", str(SCRIPT)], check=True, capture_output=True, env=env)
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


def _needs(**true):
    return {k: "true" if true.get(k) else "false" for k in ALL}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["docs/deployment.md", "README.md"], _needs()),
        (["tests/test_backups.py"], _needs()),
        (["cli/main.go"], _needs(cli=True)),
        (["installer/build-cli.sh"], _needs(cli=True, package=True)),
        (["dashboard/src/next/pages/Overview.jsx"], _needs(e2e=True)),
        (["app/routers/backups.py"], _needs(e2e=True, package=True)),
        (["cfs/src/server.c"], _needs(cfs=True, package=True)),
        (["app/core/cfs_client.py"], _needs(cfs=True, e2e=True, package=True)),
        (["tests/test_replication_qemu.py"], _needs(e2e=True)),
        (["requirements.txt"], _needs(e2e=True, package=True)),
        (["lab/scale/simulate.py", ".github/workflows/lab.yml"], _needs()),
    ],
)
def test_a_pull_request_runs_the_jobs_its_files_need(tmp_path, files, expected):
    assert _scope(tmp_path, files) == expected


def test_a_change_to_the_ci_itself_runs_everything(tmp_path):
    assert _scope(tmp_path, ["docs/x.md", ".github/workflows/ci.yml"]) == ALL
    assert _scope(tmp_path, ["scripts/ci-scope.sh"]) == ALL
    # The actions the jobs use: a change of how packages are installed reaches every job.
    assert _scope(tmp_path, [".github/actions/apt-install/action.yml"]) == ALL


def test_a_push_runs_everything(tmp_path):
    """The release reuse (scripts/ci-reuse.sh) trusts only a push run where every job ran."""
    assert _scope(tmp_path, ["docs/x.md"], event="push") == ALL


def test_any_doubt_runs_everything(tmp_path):
    assert _scope(tmp_path, ["docs/x.md"], gh_fails=True) == ALL
    assert _scope(tmp_path, []) == ALL
