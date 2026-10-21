"""app/core/paths.py: where 1.1 keeps its files, as Proxmox VE does, and the paths written up to 1.0 read there."""

import pytest

from app.core import paths


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/root/hyperlite/data/backups", paths.BACKUPS),
        ("/root/hyperlite/data/backups/web/20261001T020000Z", paths.BACKUPS / "web/20261001T020000Z"),
        ("/root/hyperlite/data/vm-exports/web.ova", paths.EXPORTS / "web.ova"),
        ("/root/hyperlite-container-backups/ct1/x.tar.zst", paths.CONTAINER_BACKUPS / "ct1/x.tar.zst"),
    ],
)
def test_a_path_written_up_to_1_0_is_read_at_its_new_place(old, new):
    assert paths.current(old) == str(new)


@pytest.mark.parametrize("path", ["/srv/backups", "/root/hyperlite/data/backups-old", "", None])
def test_any_other_path_is_left_as_it_is(path):
    assert paths.current(path) == path


def test_the_suite_never_writes_where_a_node_keeps_its_files():
    assert not str(paths.VAR).startswith("/var/lib/hyperlite") and not str(paths.ETC).startswith("/etc/hyperlite")
    assert paths.ENV_FILE.parent == paths.PRIV and paths.TLS_DIR == paths.ETC / "local"
