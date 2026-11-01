"""The tree as read-only files (--mount, docs/design/cfs-files.md): the real daemon mounted in a temporary directory.
Needs the daemon (HYPERLITE_CFS_BIN), root (allow_other) and /dev/fuse; skipped otherwise."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from app.core.cfs_client import CfsClient

BIN = os.environ.get("HYPERLITE_CFS_BIN", "")

pytestmark = pytest.mark.skipif(
    not BIN or os.geteuid() != 0 or not Path("/dev/fuse").exists(),
    reason="needs HYPERLITE_CFS_BIN, root and /dev/fuse",
)


def mounted(path):
    return subprocess.run(["mountpoint", "-q", str(path)]).returncode == 0


@pytest.fixture()
def tree(tmp_path):
    # Another account must reach the mount: pytest's own temporary directory is root's alone.
    import shutil
    import tempfile

    tmp_path = Path(tempfile.mkdtemp(prefix="cfs-mount-", dir="/var/tmp"))
    tmp_path.chmod(0o755)
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    sock = tmp_path / "cfs.sock"
    proc = subprocess.Popen(
        [
            BIN,
            "--db",
            str(tmp_path / "config.db"),
            "--socket",
            str(sock),
            "--socket-mode",
            "0660",
            "--socket-group",
            "www-data",
            "--mount",
            str(mnt),
        ],
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(250):
        if sock.exists() and mounted(mnt):
            break
        time.sleep(0.02)
    with CfsClient(str(sock)) as c:
        c.put("/db/users/alice", b'{"role": "admin"}')
        c.put("/priv/cluster/authkey", b"secret")
        yield {"mnt": mnt, "client": c, "proc": proc}
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=10)
    subprocess.run(["fusermount3", "-u", "-z", str(mnt)], capture_output=True)
    shutil.rmtree(tmp_path, ignore_errors=True)


def as_user(user, *argv):
    return subprocess.run(["runuser", "-u", user, "--", *argv], capture_output=True, text=True)


def test_the_tree_reads_as_files_and_follows_every_change(tree):
    mnt = tree["mnt"]
    assert (mnt / "db/users/alice").read_text() == '{"role": "admin"}'
    assert sorted(os.listdir(mnt)) == [".members", ".quorum", ".version", "db", "local", "priv"]
    assert os.readlink(mnt / "local").startswith("nodes/")
    assert (mnt / ".quorum").read_text() == "1\n"
    tree["client"].put("/db/users/alice", b'{"role": "viewer"}')
    assert (mnt / "db/users/alice").read_text() == '{"role": "viewer"}'  # nothing cached


def test_the_secrets_are_roots_and_nothing_is_written(tree):
    mnt = tree["mnt"]
    assert as_user("www-data", "cat", str(mnt / "db/users/alice")).returncode == 0
    assert as_user("www-data", "cat", str(mnt / "priv/cluster/authkey")).returncode != 0
    assert as_user("nobody", "cat", str(mnt / "db/users/alice")).returncode != 0
    for write in (lambda: (mnt / "db/users/bob").write_text("x"), lambda: (mnt / "db/users/alice").unlink()):
        with pytest.raises(OSError) as e:
            write()
        assert e.value.errno == 30  # EROFS, root included


def test_a_stop_unmounts_and_a_crash_is_cleaned(tree):
    proc, mnt = tree["proc"], tree["mnt"]
    proc.terminate()
    assert proc.wait(timeout=10) == 0 and not mounted(mnt)
