"""A VM moved between two other nodes (a drain or a rolling update driven from this one): its CD-ROM files (the
cloud-init ISO) are carried from the source node to the destination through this host. Found in the lab: they were
fetched here, and the destination refused the migration ("Cannot access storage file")."""

from pathlib import Path

import pytest

from app.routers.vms import migration


def test_a_file_goes_from_one_node_to_the_other_through_a_private_temporary_file(monkeypatch):
    moves = []

    def fetch(node, remote, local):
        Path(local).write_text("iso")
        moves.append(("from", node, remote, oct(Path(local).stat().st_mode & 0o777)))

    def send(local, node, remote):
        assert Path(local).read_text() == "iso"
        moves.append(("to", node, remote))
        moves.append(local)

    monkeypatch.setattr(migration, "_copy_file_from_node", fetch)
    monkeypatch.setattr(migration, "_copy_file_to_node", send)
    migration._relay_file("hlc3", "hlc2", "/var/lib/libvirt/images/web-cloudinit.iso")
    path = "/var/lib/libvirt/images/web-cloudinit.iso"
    assert moves[0] == ("from", "hlc3", path, "0o600") and moves[1] == ("to", "hlc2", path)
    assert not Path(moves[2]).exists()  # the temporary copy is removed


def test_a_failed_copy_leaves_no_temporary_file(monkeypatch):
    seen = []

    def fetch(node, remote, local):
        seen.append(local)
        raise RuntimeError("scp failed")

    monkeypatch.setattr(migration, "_copy_file_from_node", fetch)
    with pytest.raises(RuntimeError):
        migration._relay_file("hlc3", "hlc2", "/x.iso")
    assert not Path(seen[0]).exists()
