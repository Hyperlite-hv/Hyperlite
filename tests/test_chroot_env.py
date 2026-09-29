"""Commands run inside a container's filesystem get a clean environment, not the service's own."""

import re
from pathlib import Path


def test_the_service_environment_does_not_leak_into_the_container(monkeypatch):
    from app.core import container_builder

    monkeypatch.setenv("TMPDIR", "/root/hyperlite/data/tmp")  # the service's: absent inside the container
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")  # may not be installed inside the container
    env = container_builder.chroot_env()
    assert "TMPDIR" not in env
    assert env["LANG"] == env["LC_ALL"] == "C" and env["DEBIAN_FRONTEND"] == "noninteractive"
    assert env["PATH"].startswith("/usr/local/sbin")


def test_every_chroot_and_debootstrap_call_uses_it(monkeypatch, tmp_path):
    from app.core import container_builder

    seen = []
    monkeypatch.setattr(container_builder.subprocess, "run", lambda args, **kw: seen.append((args, kw.get("env"))))
    container_builder._chroot_run(tmp_path, "useradd", "-m", "demo", check=True)
    # chroot is always the program; the command's words are separate arguments, never a shell line
    assert seen[0] == (["chroot", str(tmp_path), "useradd", "-m", "demo"], container_builder.chroot_env())

    monkeypatch.setattr(container_builder, "BASE_ROOTFS", tmp_path / "base")
    container_builder.ensure_base_rootfs()
    assert seen[1][0][0] == "debootstrap" and seen[1][1] == container_builder.chroot_env()

    source = Path(container_builder.__file__).read_text()
    # the only direct chroot call is _chroot_run's own: any other would inherit the service's environment
    assert len(re.findall(r'subprocess\.run\(\s*\[\s*"chroot"', source)) == 1


def test_a_missing_tmp_is_created_for_package_scripts(tmp_path):
    from app.core import container_builder

    container_builder._ensure_tmp(tmp_path)
    assert (tmp_path / "tmp").is_dir() and ((tmp_path / "tmp").stat().st_mode & 0o7777) == 0o1777
