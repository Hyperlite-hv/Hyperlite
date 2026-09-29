"""Static checks of the installer scripts, which only run when an ISO is built."""

import re
from pathlib import Path

BUILD_ISO = Path(__file__).resolve().parents[1] / "installer" / "build-iso.sh"


def test_the_kernel_command_line_is_set_before_the_boot_menus_use_it():
    lines = BUILD_ISO.read_text().splitlines()
    assigned = [i for i, line in enumerate(lines) if re.match(r"^\s*APPEND_ARGS=", line)]
    used = [i for i, line in enumerate(lines) if "$APPEND_ARGS" in line]
    assert assigned, "APPEND_ARGS is never set: the boot entries would get an empty kernel command line"
    assert used and assigned[0] < min(used)
    value = lines[assigned[0]]
    for needed in ("auto=true", "priority=critical", "netcfg/get_hostname=", "keyboard-configuration/xkb-keymap="):
        assert needed in value
