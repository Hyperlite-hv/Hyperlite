import pytest

from app.core.safe_paths import safe_child


def test_child_inside_base(tmp_path):
    assert safe_child(tmp_path, "vm.qcow2") == tmp_path / "vm.qcow2"
    assert safe_child(tmp_path, "sub/vm.qcow2") == tmp_path / "sub" / "vm.qcow2"


@pytest.mark.parametrize("name", ["../etc/passwd", "/etc/passwd", "a/../../b", ".."])
def test_escaping_names_are_refused(tmp_path, name):
    with pytest.raises(ValueError):
        safe_child(tmp_path, name)
