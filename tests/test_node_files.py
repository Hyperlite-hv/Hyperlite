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


def test_the_package_never_takes_the_files_out_of_the_tree_and_comes_only_over_the_bridge():
    """The restore is the bridge's (1.13.1), run by its own postinst on a return to it: this version's postinst would
    take the files out of its own tree. It finds the node's keys in either place, and refuses an older version."""
    postinst = (INSTALLER / "deb" / "postinst").read_text()
    assert "node_files restore" not in postinst
    assert '[ ! -f "$ETC/local/priv/env" ]' in postinst
    preinst = (INSTALLER / "deb" / "preinst").read_text()
    assert "lt 1:1.13.1" in preinst


# ---- Step 3: the node's files in the tree (section 15) ----


class Cfs(Tree):
    """A tree that is written too, with versions: what node_files writes through the socket."""

    def __init__(self, files=None):
        super().__init__(dict(files or {}))
        self.versions = dict.fromkeys(self.files, 1)
        self.conflicts = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def get(self, path):
        if path not in self.files:
            raise cfs_client.NotFound(1, path)
        return cfs_client.Entry(data=self.files[path], version=self.versions[path], mtime=0)

    def put(self, path, data, expected=cfs_client.ANY_VERSION):
        if self.conflicts:
            self.conflicts -= 1
            raise cfs_client.Conflict(2, path)
        current = self.versions.get(path, 0)
        if expected != cfs_client.ANY_VERSION and expected != current:
            raise cfs_client.Conflict(2, path)
        self.files[path] = bytes(data)
        self.versions[path] = current + 1
        return current + 1

    def delete(self, path, expected=cfs_client.ANY_VERSION):
        if path not in self.files:
            raise cfs_client.NotFound(1, path)
        del self.files[path]


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    """/etc/hyperlite as the mount (its .version), hyperlite-cfs behind it."""
    from app.core import paths

    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / ".version").write_text("{}")
    cfs = Cfs()
    monkeypatch.setattr(paths, "ETC", etc)
    monkeypatch.setattr(node_files, "_client_factory", lambda: cfs)
    monkeypatch.setattr(node_files, "host", lambda: "pve-a")
    return cfs


def test_a_nodes_file_is_read_under_local_and_written_into_the_tree(tree):
    from app.core import paths

    etc = paths.ETC
    assert node_files.location("priv/env") == etc / "local/priv/env"
    assert node_files.location("local/hyperlite.crt") == etc / "local/hyperlite.crt"
    assert node_files.location("authkeys.json") == etc / "local/authkeys.json"
    for path in (etc / "local/priv/env", etc / "priv/env"):
        assert node_files.relative(path) == "priv/env"
    assert node_files.relative(etc / "local/authkeys.json") == "authkeys.json"
    assert node_files.relative(etc / "db/users/alice") is None and node_files.relative("/etc/passwd") is None

    node_files.write(etc / "local/priv/env", "K=1\n", 0o600)
    node_files.write(etc / "local/hyperlite.crt", b"CERT")
    assert tree.files == {"/nodes/pve-a/priv/env": b"K=1\n", "/nodes/pve-a/hyperlite.crt": b"CERT"}
    assert not (etc / "local").exists()  # nothing on the mount
    node_files.remove(etc / "local/hyperlite.crt")
    node_files.remove(etc / "local/hyperlite.crt")  # gone already: nothing to say
    assert list(tree.files) == ["/nodes/pve-a/priv/env"]
    with pytest.raises(ValueError, match="not one of the node's files"):
        node_files.write(etc / "db/users/alice", b"{}")


def test_two_writers_of_the_env_file_never_lose_each_others_line(tree):
    from app.core import paths

    env = paths.ETC / "local/priv/env"
    node_files.write(env, "A=1\n")
    tree.conflicts = 2  # another writer got there first, twice
    node_files.update(env, lambda old: old + b"B=2\n")
    assert tree.files["/nodes/pve-a/priv/env"] == b"A=1\nB=2\n"
    assert node_files.update(env, lambda old: None) == b"A=1\nB=2\n"  # nothing to change: nothing written


def test_a_key_pair_is_made_aside_and_put_in_the_tree(tree):
    from app.core import paths

    node_files.keygen(paths.ETC / "local/priv/ssh/hyperlite_cluster", "hyperlite-cluster")
    priv = tree.files["/nodes/pve-a/priv/ssh/hyperlite_cluster"]
    pub = tree.files["/nodes/pve-a/priv/ssh/hyperlite_cluster.pub"]
    assert b"PRIVATE KEY" in priv and pub.startswith(b"ssh-ed25519 ") and b"hyperlite-cluster" in pub


def test_a_removed_member_takes_its_files_with_it():
    cfs = Cfs(
        {"/nodes/PVE-B.lan/priv/env": b"x", "/nodes/PVE-B.lan/hyperlite.crt": b"c", "/nodes/pve-a/priv/env": b"a"}
    )
    node_files.drop_node("pve-b", client=cfs)
    assert list(cfs.files) == ["/nodes/pve-a/priv/env"]


def _node_on_disk(etc):
    (etc / "local").mkdir(parents=True)
    (etc / "priv/ssh").mkdir(parents=True)
    (etc / "cfs").mkdir()
    (etc / "local/hyperlite.crt").write_text("CERT")
    (etc / "priv/env").write_text("HYPERLITE_SECRET_KEY=k\n")
    (etc / "priv/ssh/hyperlite_cluster").write_text("SSH")
    (etc / "authkeys.json").write_text("[]")
    (etc / "access.db").write_text("db")
    (etc / "access.db-wal").write_text("wal")


class Services:
    """systemctl as the move sees it: hyperlite-cfs mounts the tree over the empty /etc/hyperlite when it starts."""

    def __init__(self, etc, mounts=True):
        self.etc, self.mounts, self.calls = etc, mounts, []

    def __call__(self, *args):
        self.calls.append(" ".join(args))
        if args == ("start", "hyperlite-cfs.service") and self.mounts and not any(self.etc.iterdir()):
            (self.etc / ".version").write_text("{}")
        return True


def _virsh_with(pools):
    defined = {}

    def virsh(*args, data=None):
        if args[0] == "pool-list":
            return subprocess.CompletedProcess(args, 0, "\n".join(pools), "")
        if args[0] == "pool-dumpxml":
            return subprocess.CompletedProcess(args, 0, pools[args[-1]], "")
        defined[args[0]] = Path(args[1]).read_text()
        return subprocess.CompletedProcess(args, 0, "", "")

    virsh.defined = defined
    return virsh


def test_the_move_puts_every_file_in_the_tree_and_mounts_it_in_their_place(tmp_path, monkeypatch):
    from app.core import api_group

    monkeypatch.setattr(api_group, "share", lambda path, mode: os.chmod(path, mode))
    etc, var = tmp_path / "etc", tmp_path / "var"
    var.mkdir()
    _node_on_disk(etc)
    cfs = Cfs({"/nodes/pve-a/priv/old-key": b"left by an earlier move"})
    services = Services(etc)
    smb = f"<pool><option name='credentials={etc}/priv/smb/nas.cred'/></pool>"
    virsh = _virsh_with({"nas": smb})
    moved = node_files.move(etc, var, cfs, "pve-a", services, virsh, log=lambda m: None, wait_s=1)

    assert moved == ["authkeys.json", "local/hyperlite.crt", "priv/env", "priv/ssh/hyperlite_cluster"]
    assert cfs.files == {
        "/nodes/pve-a/authkeys.json": b"[]",
        "/nodes/pve-a/hyperlite.crt": b"CERT",
        "/nodes/pve-a/priv/env": b"HYPERLITE_SECRET_KEY=k\n",
        "/nodes/pve-a/priv/ssh/hyperlite_cluster": b"SSH",
    }
    assert services.calls == [
        "stop hyperlite-daemon.service",
        "stop hyperlite-cfs.service",
        "start hyperlite-cfs.service",
        "start hyperlite-daemon.service",
    ]
    assert node_files.in_tree(etc) and [p.name for p in etc.iterdir()] == [".version"]
    assert (var / "access/access.db").read_text() == "db" and (var / "access/access.db-wal").read_text() == "wal"
    assert os.stat(var / "access").st_mode & 0o777 == 0o750
    (kept,) = var.glob("etc-before-cfs-*")
    assert (kept / "priv/env").exists() and os.stat(kept).st_mode & 0o777 == 0o700
    assert f"credentials={etc}/local/priv/smb/nas.cred" in virsh.defined["pool-define"]
    # Done once: the next start finds the mount and does nothing.
    assert node_files.move(etc, var, cfs, "pve-a", services, virsh, log=lambda m: None) == []


def test_a_mount_that_does_not_come_puts_everything_back(tmp_path, monkeypatch):
    from app.core import api_group

    monkeypatch.setattr(api_group, "share", lambda path, mode: os.chmod(path, mode))
    etc, var = tmp_path / "etc", tmp_path / "var"
    var.mkdir()
    _node_on_disk(etc)
    services = Services(etc, mounts=False)
    with pytest.raises(RuntimeError, match="did not mount the tree"):
        node_files.move(etc, var, Cfs(), "pve-a", services, _virsh_with({}), log=lambda m: None, wait_s=0.2)
    assert (etc / "priv/env").read_text() == "HYPERLITE_SECRET_KEY=k\n" and (etc / "access.db").read_text() == "db"
    assert not list(var.glob("etc-before-cfs-*")) and not (var / "access/access.db").exists()
    assert services.calls[-2:] == ["start hyperlite-cfs.service", "start hyperlite-daemon.service"]


def test_a_node_without_its_keys_is_not_moved(tmp_path):
    etc = tmp_path / "etc"
    (etc / "local").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="priv/env is missing"):
        node_files.move(etc, tmp_path, Cfs(), "pve-a", Services(etc), _virsh_with({}), log=lambda m: None)


def test_the_writers_of_the_nodes_files_write_into_the_tree(tree, monkeypatch):
    """Once /etc/hyperlite is the mount, nothing is written on it: each writer puts its file in the tree."""
    from app.core import paths, secrets_crypto, tickets, tls_certs

    etc = paths.ETC
    monkeypatch.setattr(tls_certs, "TLS_DIR", etc / "local")
    monkeypatch.setattr(tls_certs, "_check_loads", lambda cert, key: None)
    tls_certs.install(b"CERT", b"KEY", "import")
    assert tree.files["/nodes/pve-a/hyperlite.crt"] == b"CERT" and tree.files["/nodes/pve-a/hyperlite.key"] == b"KEY"
    assert tree.files["/nodes/pve-a/source"] == b"import\n"

    monkeypatch.setattr(secrets_crypto, "ENV_PATH", etc / "local/priv/env")
    monkeypatch.delenv(secrets_crypto._KEY_VAR, raising=False)
    tree.files["/nodes/pve-a/priv/env"] = b"HYPERLITE_SECRET_KEY=s\n"
    tree.versions["/nodes/pve-a/priv/env"] = 1
    key = secrets_crypto._load_or_create_key()
    monkeypatch.delenv(secrets_crypto._KEY_VAR, raising=False)
    assert secrets_crypto._load_or_create_key() == key  # read back, never a second key
    env = tree.files["/nodes/pve-a/priv/env"].decode()
    assert env.startswith("HYPERLITE_SECRET_KEY=s\n") and env.count(f"{secrets_crypto._KEY_VAR}=") == 1

    tickets._write(etc / "local/authkeys.json", b'{"keys": []}', 0o644)
    assert tree.files["/nodes/pve-a/authkeys.json"] == b'{"keys": []}'
    assert not (etc / "local").exists()


def test_a_node_whose_tree_did_not_mount_is_told_so(tmp_path):
    """After the move /etc/hyperlite is empty beneath the mount: when hyperlite-cfs did not mount it, nothing is moved
    and the reason is said."""
    etc, var = tmp_path / "etc", tmp_path / "var"
    etc.mkdir()
    (var / "etc-before-cfs-20261101T000000Z").mkdir(parents=True, mode=0o700)
    with pytest.raises(RuntimeError, match="hyperlite-cfs has not mounted the tree"):
        node_files.move(etc, var, Cfs(), "pve-a", Services(etc), _virsh_with({}), log=lambda m: None)


def test_the_tree_mounted_at_cfs_is_never_walked(tmp_path):
    etc = tmp_path / "etc"
    _node_on_disk(etc)
    (etc / "cfs" / "priv").mkdir(parents=True)
    (etc / "cfs" / "priv" / "authkey").write_text("the cluster's")
    files, other = node_files.on_disk(etc)
    assert sorted(files) == ["authkeys.json", "local/hyperlite.crt", "priv/env", "priv/ssh/hyperlite_cluster"]
    assert other == []
