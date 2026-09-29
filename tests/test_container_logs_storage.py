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
def test_only_a_started_local_directory_pool_can_hold_containers(tmp_path, monkeypatch, pool, error):
    from app.core import libvirt_utils
    from app.routers import containers

    pools = {
        "fast": _Pool("dir", tmp_path / "ssd"),
        "nas": _Pool("netfs", "/mnt/nas"),
        "off": _Pool("dir", "/x", active=False),
    }
    monkeypatch.setattr(libvirt_utils, "open_conn", lambda node=None: _QemuConn(pools))
    if error:
        with pytest.raises(ValueError, match=error):
            containers.resolve_container_storage(pool)
    else:
        assert containers.resolve_container_storage(pool) == {
            "pool": "fast",
            "base_dir": str(tmp_path / "ssd" / "hyperlite-containers"),
        }


def test_a_container_lives_in_its_pool_and_its_clone_follows(database, tmp_path, monkeypatch):
    from app.core import container_builder
    from app.core.container_meta import get_container_storage

    monkeypatch.setattr(container_builder, "CONTAINERS_DIR", tmp_path / "default")
    base = tmp_path / "base-rootfs"
    (base / "bin").mkdir(parents=True)
    (base / "bin" / "sh").write_text("")
    monkeypatch.setattr(container_builder, "ensure_base_rootfs", lambda: base)
    monkeypatch.setattr(container_builder, "_reset_container_identity", lambda dest, name: None)
    storage = {"pool": "fast", "base_dir": str(tmp_path / "ssd" / "hyperlite-containers")}

    rootfs, _ = container_builder.create_container_rootfs("web", storage=storage)
    assert rootfs == tmp_path / "ssd" / "hyperlite-containers" / "web" and (rootfs / "bin" / "sh").exists()
    assert container_builder.container_rootfs_path("web") == rootfs
    clone = container_builder.clone_container_rootfs("web", "web2")
    assert clone.parent == rootfs.parent and get_container_storage("web2")["pool"] == "fast"

    container_builder.delete_container_rootfs("web")
    assert not rootfs.exists() and get_container_storage("web") is None
    # a container without a chosen pool keeps the default place
    assert container_builder.container_rootfs_path("other") == tmp_path / "default" / "other"
