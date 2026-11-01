"""The bridge (app/core/node_files.py, docs/design/cfs-files.md section 8): a rollback from step 3 puts the node's files
back in /etc/hyperlite from the cluster's tree and the directory step 3 kept, before the postinst looks for them."""

import os
import subprocess
from pathlib import Path

import pytest

from app.core import cfs_client, node_files

INSTALLER = Path(__file__).resolve().parents[1] / "installer"


class Tree:
    """hyperlite-cfs's tree as cfs_client reads it: {"/nodes/pve-a/hyperlite.crt": b"..."}."""

    def __init__(self, files):
        self.files = files

    def list(self, path):
        prefix = path.rstrip("/") + "/"
        names = {}
        for p in self.files:
            if p.startswith(prefix):
                rest = p[len(prefix) :].split("/")
                names[rest[0]] = len(rest) > 1
        if not names:
            raise cfs_client.NotFound(1, path)
        return [cfs_client.Child(name=n, is_dir=d, version=1, size=0) for n, d in sorted(names.items())]

    def get(self, path):
        return cfs_client.Entry(data=self.files[path], version=1, mtime=0)


def restore(tmp_path, tree=None, mounted=None, **kw):
    etc, var = tmp_path / "etc", tmp_path / "var"
    var.mkdir(exist_ok=True)
    state = {"mounted": bool(mounted), "unmounts": 0}

    def unmount(path):
        state["unmounts"] += 1
        state["mounted"] = mounted == "stays"

    written = node_files.restore(
        etc=etc,
        var=var,
        client=Tree(tree or {}),
        node="pve-a",
        is_mount=lambda p: state["mounted"],
        unmount=unmount,
        log=lambda m: None,
        **{"virsh": lambda *a, **k: subprocess.CompletedProcess(a, 1, "", ""), **kw},
    )
    return etc, var, written, state


def test_each_file_has_one_place_in_the_tree_and_back():
    for rel, tree in (
        ("local/hyperlite.crt", "/nodes/pve-a/hyperlite.crt"),
        ("local/vlan-aware-bridges", "/nodes/pve-a/vlan-aware-bridges"),
        ("priv/env", "/nodes/pve-a/priv/env"),
        ("priv/ssh/hyperlite_cluster", "/nodes/pve-a/priv/ssh/hyperlite_cluster"),
        ("authkeys.json", "/nodes/pve-a/authkeys.json"),
    ):
        assert node_files.tree_path(rel, "pve-a") == tree
        assert node_files.disk_path(tree[len("/nodes/pve-a/") :]) == rel
    for rel in ("access.db", "cfs/db/x", "local", "priv", "../etc/passwd", "local/../../x"):
        assert node_files.tree_path(rel, "pve-a") is None


def test_a_rollback_puts_the_files_back_from_the_tree_with_their_modes(tmp_path):
    etc, _, written, state = restore(
        tmp_path,
        {
            "/nodes/pve-a/hyperlite.crt": b"CERT",
            "/nodes/pve-a/hyperlite.key": b"KEY",
            "/nodes/pve-a/authkeys.json": b"[]",
            "/nodes/pve-a/priv/env": b"HYPERLITE_SECRET_KEY=x\n",
            "/nodes/pve-a/priv/ssh/hyperlite_cluster": b"SSH",
            "/nodes/pve-b/priv/env": b"another node's",
        },
        mounted=True,
    )
    assert state["unmounts"] == 1  # the tree mounted over /etc/hyperlite comes off first
    assert (etc / "priv/env").read_bytes() == b"HYPERLITE_SECRET_KEY=x\n"
    assert (etc / "local/hyperlite.crt").read_bytes() == b"CERT"
    assert sorted(written) == [
        "authkeys.json",
        "local/hyperlite.crt",
        "local/hyperlite.key",
        "priv/env",
        "priv/ssh/hyperlite_cluster",
    ]
    mode = lambda rel: os.stat(etc / rel).st_mode & 0o777  # noqa: E731
    assert mode("priv") == 0o700 and mode("priv/ssh") == 0o700 and mode("priv/env") == 0o600
    assert mode("local") == 0o750 and mode("local/hyperlite.key") == 0o640 and mode("local/hyperlite.crt") == 0o644
    assert not list(etc.rglob(".*.restore"))


def test_a_file_on_disk_is_never_replaced_and_the_kept_directory_fills_the_gaps(tmp_path):
    etc = tmp_path / "etc"
    (etc / "priv").mkdir(parents=True)
    (etc / "priv/env").write_text("the node's own")
    kept = tmp_path / "var" / "etc-before-cfs-20261101"
    (kept / "local").mkdir(parents=True)
    (kept / "local/openssl-hyperlite.cnf").write_text("cnf")
    os.chmod(kept / "local/openssl-hyperlite.cnf", 0o644)
    (kept / "priv").mkdir()
    (kept / "priv/env").write_text("older")
    (kept / "access.db").write_text("kept db")
    (tmp_path / "var" / "etc-before-cfs-20260101").mkdir()  # an older one is not read
    etc, _, written, _ = restore(tmp_path, {"/nodes/pve-a/priv/env": b"tree"})
    assert (etc / "priv/env").read_text() == "the node's own"
    assert (etc / "local/openssl-hyperlite.cnf").read_text() == "cnf"
    assert (etc / "access.db").read_text() == "kept db"
    assert sorted(written) == ["access.db", "local/openssl-hyperlite.cnf"]


def test_the_access_database_comes_back_with_its_journal(tmp_path):
    moved = tmp_path / "var" / "access"
    moved.mkdir(parents=True, mode=0o750)
    for suffix in ("", "-wal", "-shm"):
        (moved / f"access.db{suffix}").write_text(suffix or "db")
    etc, _, written, _ = restore(tmp_path)
    assert written == ["access.db"]
    assert sorted(p.name for p in etc.iterdir()) == ["access.db", "access.db-shm", "access.db-wal"]
    assert not list(moved.glob("access.db*"))


def test_what_the_apis_group_could_have_made_is_never_trusted(tmp_path):
    """/var/lib/hyperlite is writable by www-data: a kept directory or a database it could have planted or changed is
    left alone, so a compromised API cannot give itself keys or permissions through a rollback."""
    var = tmp_path / "var"
    planted = var / "etc-before-cfs-99991231"
    (planted / "priv").mkdir(parents=True)
    (planted / "priv/env").write_text("HYPERLITE_SECRET_KEY=the attacker's")
    (planted / "access.db").write_text("forged")
    os.chmod(planted, 0o777)  # noqa: S103 (what www-data could make)
    moved = var / "access"
    moved.mkdir(mode=0o770)
    os.chmod(moved, 0o770)  # noqa: S103
    (moved / "access.db").write_text("forged")
    (var / "access.db").write_text("forged")  # where 1.13.0 looked
    etc, _, written, _ = restore(tmp_path)
    assert written == [] and not (etc / "priv/env").exists() and not (etc / "access.db").exists()


def test_smb_pools_read_their_credentials_where_the_files_are_put_back(tmp_path):
    defined = {}

    def virsh(*args, data=None):
        if args[0] == "pool-list":
            return subprocess.CompletedProcess(args, 0, "nas\nlocal\n", "")
        if args[0] == "pool-dumpxml":
            option = "credentials=/x/etc/local/priv/smb/nas.cred" if args[-1] == "nas" else "noperm"
            return subprocess.CompletedProcess(args, 0, f"<pool><option name='{option}'/></pool>", "")
        defined["xml"] = Path(args[1]).read_text()
        return subprocess.CompletedProcess(args, 0, "", "")

    assert node_files.smb_pools("/x/etc/local/priv/smb", "/x/etc/priv/smb", virsh) == ["nas"]
    assert "credentials=/x/etc/priv/smb/nas.cred" in defined["xml"]


def test_nothing_to_do_on_an_ordinary_node(tmp_path):
    etc = tmp_path / "etc"
    (etc / "priv").mkdir(parents=True)
    (etc / "priv/env").write_text("keys")
    _, _, written, state = restore(tmp_path)
    assert written == [] and state["unmounts"] == 0


def test_a_mount_that_stays_stops_the_installation(tmp_path):
    with pytest.raises(RuntimeError, match="still mounted"):
        restore(tmp_path, {"/nodes/pve-a/priv/env": b"x"}, mounted="stays")
    assert not (tmp_path / "etc" / "priv").exists()


def test_the_postinst_puts_them_back_before_it_looks_for_the_nodes_keys():
    postinst = (INSTALLER / "deb" / "postinst").read_text()
    assert postinst.index("python3 -m app.core.node_files restore") < postinst.index('[ ! -f "$PRIV/env" ]')
