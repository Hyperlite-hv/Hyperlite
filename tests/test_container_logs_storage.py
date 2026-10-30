"""Docker containers keep what their process prints; containers can live in a chosen directory storage pool."""

import os
import shutil
import subprocess

import pytest

gcc = pytest.mark.skipif(shutil.which("gcc") is None, reason="needs gcc to build the launcher")


@gcc
def test_the_launcher_captures_output_and_hands_over_to_the_program(tmp_path, monkeypatch):
    from app.core import container_console

    monkeypatch.setattr(container_console, "BIN_DIR", tmp_path / "bin")
    binary = container_console.launcher_binary()
    if binary is None:
        pytest.skip("static linking not available here")
    log = tmp_path / "console.log"
    r = subprocess.run([str(binary), str(log), "sh", "-c", "echo out; echo err >&2; exit 3"], capture_output=True)
    assert r.returncode == 3  # exec: the program's own exit code, not the launcher's
    text = log.read_text()
    assert "--- hyperlite: starting sh" in text and "out" in text and "err" in text
    r = subprocess.run([str(binary), str(log), "no-such-program-xyz"], capture_output=True)
    assert r.returncode == 127 and "cannot start no-such-program-xyz" in log.read_text()


def test_the_log_is_read_from_its_tail_and_trimmed(tmp_path, monkeypatch):
    from app.core import container_console

    rootfs = tmp_path / "rootfs"
    (rootfs / ".hyperlite").mkdir(parents=True)
    assert container_console.read_tail(rootfs) is None  # no log: created before logs existed
    log = rootfs / ".hyperlite" / "console.log"
    log.write_text("".join(f"line {i}\n" for i in range(1000)))
    assert container_console.read_tail(rootfs, lines=3) == ["line 997", "line 998", "line 999"]
    monkeypatch.setattr(container_console, "MAX_BYTES", 100)
    monkeypatch.setattr(container_console, "KEEP_BYTES", 30)
    container_console.trim(rootfs)
    assert log.stat().st_size < 200 and log.read_text().endswith("line 999\n")


def test_a_link_in_the_image_never_redirects_the_launcher_files(tmp_path, monkeypatch):
    from app.core import container_console

    monkeypatch.setattr(container_console, "launcher_binary", lambda: tmp_path / "fake-bin")
    (tmp_path / "fake-bin").write_bytes(b"\x7fELF")
    outside = tmp_path / "outside"
    outside.mkdir()
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / ".hyperlite").symlink_to(outside)
    assert container_console.install(rootfs, os.getuid(), os.getgid()) is True
    assert not (rootfs / ".hyperlite").is_symlink() and list(outside.iterdir()) == []
    assert (rootfs / ".hyperlite" / "hl-console").exists() and (rootfs / ".hyperlite" / "console.log").exists()


def test_the_log_is_never_read_or_trimmed_through_a_link(tmp_path, monkeypatch):
    # The container's root owns .hyperlite: a link there, at either level, must never reach a host file, which this
    # process (root) would read to the user or truncate (GHSA-fvvw-286q-82jj).
    from app.core import container_console

    monkeypatch.setattr(container_console, "MAX_BYTES", 10)
    secret = tmp_path / "host" / "console.log"
    secret.parent.mkdir()
    secret.write_text("a host secret\n" * 10)
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / ".hyperlite").symlink_to(secret.parent)  # the directory itself a link
    assert container_console.read_tail(rootfs) is None
    (rootfs / ".hyperlite").unlink()
    (rootfs / ".hyperlite").mkdir()
    (rootfs / ".hyperlite" / "console.log").symlink_to(secret)  # the log a link
    assert container_console.read_tail(rootfs) is None
    container_console.trim(rootfs)
    assert secret.read_text() == "a host secret\n" * 10


class _Pool:
    def __init__(self, kind, path, active=True):
        self.kind, self.path, self.active = kind, path, active

    def XMLDesc(self, flags=0):
        return f"<pool type='{self.kind}'><name>p</name><target><path>{self.path}</path></target></pool>"

    def isActive(self):
        return self.active


class _QemuConn:
    def __init__(self, pools):
        self.pools = pools

    def storagePoolLookupByName(self, name):
        import libvirt

        if name not in self.pools:
            raise libvirt.libvirtError("no pool")
        return self.pools[name]

    def close(self):
        pass


@pytest.mark.parametrize(
    ("pool", "error"),
    [("fast", None), ("nas", "not a local directory"), ("off", "not started"), ("ghost", "not found")],
)
def test_only_a_started_local_directory_pool_can_hold_containers(tmp_path, pool, error):
    from app.core import container_create

    pools = {
        "fast": _Pool("dir", tmp_path / "ssd"),
        "nas": _Pool("netfs", "/mnt/nas"),
        "off": _Pool("dir", "/x", active=False),
    }
    if error:
        with pytest.raises(container_create.ContainerError, match=error):
            container_create.home_for(_QemuConn(pools), pool)
    else:
        assert container_create.home_for(_QemuConn(pools), pool) == (tmp_path / "ssd" / "hyperlite-containers", "fast")


def test_a_container_lives_in_its_pool_and_its_clone_follows(database, tmp_path, monkeypatch):
    from app.core import container_builder
    from app.core.container_meta import set_container_storage

    monkeypatch.setattr(container_builder, "CONTAINERS_DIR", tmp_path / "default")
    base = tmp_path / "base-rootfs"
    (base / "bin").mkdir(parents=True)
    (base / "bin" / "sh").write_text("")
    monkeypatch.setattr(container_builder, "ensure_base_rootfs", lambda: base)
    monkeypatch.setattr(container_builder, "_reset_container_identity", lambda dest, name: None)
    home = tmp_path / "ssd" / "hyperlite-containers"

    rootfs, _ = container_builder.create_container_rootfs("web", home=home)
    assert rootfs == home / "web" and (rootfs / "bin" / "sh").exists()
    set_container_storage("web", "fast", str(home))  # what the API records once hyperlite-daemon made it
    assert container_builder.container_rootfs_path("web") == rootfs
    clone = container_builder.clone_container_rootfs(rootfs, home / "web2")
    assert clone.parent == rootfs.parent and (clone / "bin" / "sh").exists()
    # a container without a chosen pool keeps the default place
    assert container_builder.container_rootfs_path("other") == tmp_path / "default" / "other"
    assert container_builder.create_container_rootfs("other")[0] == tmp_path / "default" / "other"
