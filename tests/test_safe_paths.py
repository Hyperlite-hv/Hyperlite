import pytest

from app.core.safe_paths import safe_child


def test_child_inside_base(tmp_path):
    assert safe_child(tmp_path, "vm.qcow2") == tmp_path / "vm.qcow2"
    assert safe_child(tmp_path, "sub/vm.qcow2") == tmp_path / "sub" / "vm.qcow2"


@pytest.mark.parametrize("name", ["../etc/passwd", "/etc/passwd", "a/../../b", ".."])
def test_escaping_names_are_refused(tmp_path, name):
    with pytest.raises(ValueError):
        safe_child(tmp_path, name)


def test_a_symbolic_link_out_of_the_base_is_refused(tmp_path):
    base = tmp_path / "isos"
    base.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (base / "escape").symlink_to(outside)
    (base / "escape.iso").symlink_to(outside / "file")
    with pytest.raises(ValueError, match="symbolic link"):
        safe_child(base, "escape/shadow")
    with pytest.raises(ValueError, match="symbolic link"):
        safe_child(base, "escape.iso")


def test_a_symbolic_link_that_stays_inside_is_fine(tmp_path):
    base = tmp_path / "isos"
    (base / "real").mkdir(parents=True)
    (base / "alias").symlink_to(base / "real")
    assert safe_child(base, "alias/x.iso") == base / "alias" / "x.iso"
