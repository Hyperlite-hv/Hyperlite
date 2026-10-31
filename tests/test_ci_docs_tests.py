"""The backend tests of a `docs` run (scripts/ci-docs-tests.sh): only release files changed since a tree whose whole
suite passed, so the tests that read those files run again, and they alone."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _selected():
    out = subprocess.run(["bash", "scripts/ci-docs-tests.sh"], cwd=ROOT, capture_output=True, text=True, check=True)
    return set(out.stdout.split())


def test_the_tests_of_the_release_files_are_selected():
    selected = _selected()
    for name in ("test_changelog.py", "test_release_notes.py", "test_proxmox_parity.py", "test_ci_reuse.py"):
        assert f"tests/{name}" in selected
    assert all((ROOT / path).is_file() for path in selected)


def test_a_docs_run_does_not_take_the_whole_suite():
    assert len(_selected()) * 4 < len(list((ROOT / "tests").glob("test_*.py")))


def test_the_backend_job_runs_them_on_a_docs_run_and_the_whole_suite_otherwise():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert (
        "if: needs.reuse.outputs.mode == 'docs'\n        run: mapfile -t files < <(bash scripts/ci-docs-tests.sh)" in ci
    )
    assert (
        "if: needs.reuse.outputs.mode != 'docs'\n        run: HYPERLITE_CFS_BIN=build-cfs/hyperlite-cfs python -m pytest -q -n 4"
        in ci
    )
