"""The gh-pages publication must carry the signed index files, even from inside a git hook."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish-gh-pages.sh"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.test",
}
INDEX_FILES = {
    "dists/stable/Release": "release",
    "dists/stable/InRelease": "inrelease",
    "dists/stable/Release.gpg": "gpg",
    "dists/stable/main/binary-amd64/Packages": "packages",
    "dists/stable/main/binary-amd64/Packages.gz": "packagesgz",
}


def _run(args, cwd=None, env=None):
    return subprocess.run(
        args, cwd=cwd, env={**os.environ, **GIT_ENV, **(env or {})}, check=True, capture_output=True, text=True
    )


def _write_repo(root: Path, generation: str):
    for rel, base in INDEX_FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same length on purpose: a size-only comparison would consider the files unchanged.
        target.write_text(f"{base}-{generation}\n")
    (root / "pool").mkdir(exist_ok=True)
    (root / "pool" / "hyperlite.deb").write_text(f"deb-{generation}\n")


def _make_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    _run(["git", "init", "-q", "--bare", "-b", "gh-pages", str(remote)])
    seed = tmp_path / "seed"
    seed.mkdir()
    _run(["git", "init", "-q", "-b", "gh-pages"], cwd=seed)
    _write_repo(seed, "1")
    _run(["git", "add", "-A"], cwd=seed)
    _run(["git", "commit", "-q", "-m", "seed"], cwd=seed)
    _run(["git", "push", "-q", str(remote), "gh-pages"], cwd=seed)
    return remote


def _published(remote: Path, rel: str) -> str:
    return _run(["git", "--git-dir", str(remote), "show", f"gh-pages:{rel}"]).stdout


def test_every_index_file_is_published_even_with_hook_git_variables(tmp_path):
    remote = _make_remote(tmp_path)
    built = tmp_path / "apt-repo"
    _write_repo(built, "2")

    # What `git pull` exports to its hooks: it must not leak into the mirror commit.
    hostile = {"GIT_DIR": str(tmp_path / "elsewhere.git"), "GIT_INDEX_FILE": str(tmp_path / "elsewhere.index")}
    result = _run(["bash", str(SCRIPT), str(built), str(remote), "2026.01.01.0000", "abc12345"], env=hostile)

    assert "verified" in result.stdout
    for rel, base in INDEX_FILES.items():
        assert _published(remote, rel) == f"{base}-2\n", rel


def test_a_second_publication_with_identical_sizes_still_updates_everything(tmp_path):
    remote = _make_remote(tmp_path)
    built = tmp_path / "apt-repo"
    for generation in ("2", "3"):
        _write_repo(built, generation)
        _run(["bash", str(SCRIPT), str(built), str(remote), f"v{generation}", "abc"])
    assert _published(remote, "dists/stable/Release") == "release-3\n"
    assert _published(remote, "dists/stable/InRelease") == "inrelease-3\n"


def test_nothing_is_committed_when_nothing_changed(tmp_path):
    remote = _make_remote(tmp_path)
    built = tmp_path / "apt-repo"
    _write_repo(built, "1")
    _run(["bash", str(SCRIPT), str(built), str(remote), "v1", "abc"])
    log = _run(["git", "--git-dir", str(remote), "log", "--oneline", "gh-pages"]).stdout.strip().splitlines()
    assert len(log) == 1


def test_a_relative_source_path_is_accepted(tmp_path):
    remote = _make_remote(tmp_path)
    built = tmp_path / "apt-repo"
    _write_repo(built, "2")
    result = _run(["bash", str(SCRIPT), "apt-repo", str(remote), "v2", "abc"], cwd=tmp_path)
    assert "verified" in result.stdout
    assert _published(remote, "dists/stable/Release") == "release-2\n"


def test_it_commits_even_when_git_has_no_identity(tmp_path):
    remote = _make_remote(tmp_path)
    built = tmp_path / "apt-repo"
    _write_repo(built, "2")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("GIT_AUTHOR", "GIT_COMMITTER", "EMAIL"))
    }
    env.update({"HOME": str(home), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    result = subprocess.run(
        ["bash", str(SCRIPT), str(built), str(remote), "v2", "abc"], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert _published(remote, "dists/stable/Release") == "release-2\n"
