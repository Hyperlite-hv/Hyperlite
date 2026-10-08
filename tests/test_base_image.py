"""The base cloud image is downloaded once, under a temporary name: a VM created during the download used a truncated
image, and a failed download left it for every later VM."""

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.core import vm_builder


@pytest.fixture()
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(vm_builder, "BASE_IMAGE", tmp_path / "base" / "debian.qcow2")
    calls = []

    def run(cmd, **kw):
        if cmd[0] == "wget":
            calls.append(cmd)
            time.sleep(0.2)  # long enough for the others to arrive meanwhile
            if run.fail:
                Path(cmd[3]).write_text("half")
                raise subprocess.CalledProcessError(4, cmd)
            Path(cmd[3]).write_text("qcow2 bytes")
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"format": run.fmt}), "")

    run.fail, run.fmt = False, "qcow2"
    monkeypatch.setattr(vm_builder.subprocess, "run", run)
    return run, calls


def test_concurrent_creations_share_one_complete_download(base):
    _run, calls = base
    seen = []

    def create():
        path = vm_builder.ensure_base_image()
        seen.append(path.read_text())

    threads = [threading.Thread(target=create) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and seen == ["qcow2 bytes"] * 4
    assert not vm_builder.BASE_IMAGE.with_name("debian.qcow2.part").exists()


@pytest.mark.parametrize("problem", ["download fails", "not a qcow2 image"])
def test_a_failed_download_leaves_nothing_behind(base, problem):
    run, _ = base
    run.fail, run.fmt = problem == "download fails", "raw"
    with pytest.raises((subprocess.CalledProcessError, RuntimeError)):
        vm_builder.ensure_base_image()
    assert list(vm_builder.BASE_IMAGE.parent.iterdir()) == []


def test_a_disk_on_another_pool_is_based_on_a_copy_of_the_base_in_that_pool(base, tmp_path, monkeypatch):
    """On a shared pool, a base image left in this node's images directory could not be opened by the other nodes:
    the VM could not migrate ("Cannot access backing file")."""
    images = tmp_path / "images"
    images.mkdir()
    monkeypatch.setattr(vm_builder, "IMAGES_DIR", images)
    pool = tmp_path / "pool"
    pool.mkdir()
    created = []
    _run, _ = base
    real = vm_builder.subprocess.run

    def recording(cmd, **kw):
        if cmd[:2] == ["qemu-img", "create"]:
            created.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real(cmd, **kw)

    monkeypatch.setattr(vm_builder.subprocess, "run", recording)
    vm_builder.create_disk("web", 8, target_dir=pool)
    vm_builder.create_disk("db", 8, target_dir=pool)
    in_pool = pool / vm_builder.POOL_BASE_NAME
    assert in_pool.read_text() == "qcow2 bytes"
    assert [c[c.index("-b") + 1] for c in created] == [str(in_pool), str(in_pool)]
    vm_builder.create_disk("local", 8, target_dir=images)
    assert created[-1][created[-1].index("-b") + 1] == str(vm_builder.BASE_IMAGE)
