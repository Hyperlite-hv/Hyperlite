"""Static checks of the installer scripts, which only run when an ISO is built."""

import re
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "installer"
BUILD_ISO = INSTALLER / "build-iso.sh"


def _assignments(lines):
    """name -> (line index, value) of the shell variables build-iso.sh sets."""
    out = {}
    for i, line in enumerate(lines):
        m = re.match(r'^\s*([A-Z_]+)="(.*)"\s*$', line)
        if m and m.group(1) not in out:
            out[m.group(1)] = (i, m.group(2))
    return out


def _expand(name, assigned):
    value = assigned[name][1]
    return re.sub(r"\$([A-Z_]+)", lambda m: _expand(m.group(1), assigned), value)


def test_each_boot_entry_gets_its_kernel_command_line():
    lines = BUILD_ISO.read_text().splitlines()
    assigned = _assignments(lines)
    for name in ("ARGS_AUTO_FR", "ARGS_AUTO_EN", "ARGS_CUSTOM_FR", "ARGS_CUSTOM_EN"):
        # Set before any boot menu uses it: an unset variable expands to nothing, and the ISO boots with an empty
        # kernel command line (it happened once).
        assert name in assigned, f"{name} is never set"
        used = [i for i, line in enumerate(lines) if f"${name}" in line]
        assert used, f"{name} is set but no boot entry uses it"
        assert assigned[name][0] < min(used)
        # Twice: the BIOS (isolinux) entry and the UEFI (grub) one.
        assert len(used) >= 2, f"{name} is not used by both boot menus"

    for name in ("ARGS_AUTO_FR", "ARGS_AUTO_EN"):
        value = _expand(name, assigned)
        for needed in (
            "auto=true",
            "priority=critical",
            "hlmode=auto",
            "netcfg/get_hostname=",
            "keyboard-configuration/xkb-keymap=",
        ):
            assert needed in value, f"{needed} missing from {name}"
    for name in ("ARGS_CUSTOM_FR", "ARGS_CUSTOM_EN"):
        value = _expand(name, assigned)
        assert "priority=high" in value and "hlmode=custom" in value
        # auto=true skips the keyboard question, which the custom mode must ask.
        assert "auto=true" not in value
        assert "keyboard-configuration/xkb-keymap=" not in value
    # Without debconf/language on the command line, the installer's screens stay in English.
    assert "debconf/language=fr" in _expand("ARGS_AUTO_FR", assigned)
    assert "debconf/language=fr" in _expand("ARGS_CUSTOM_FR", assigned)
    assert "debconf/language=en" in _expand("ARGS_AUTO_EN", assigned)


def test_the_bios_menu_does_not_include_debians_own():
    """Debian's menu files carry "default" lines of their own, read after ours: its entry booted instead."""
    text = BUILD_ISO.read_text()
    isolinux = text[text.index('cat > "$EXTRACT_DIR/isolinux/isolinux.cfg"') : text.index("# ---- UEFI (grub) ----")]
    assert "include menu.cfg" not in isolinux
    assert "default hyperlite\n" in isolinux and "prompt 1" in isolinux


def test_each_mode_has_its_preseed_and_the_disk_is_chosen_when_the_cd_is_mounted():
    common = (INSTALLER / "preseed.cfg").read_text()
    auto = (INSTALLER / "preseed-auto.cfg").read_text()
    custom = (INSTALLER / "preseed-custom.cfg").read_text()
    # include_command runs from the initrd's preseed before the CD is mounted: partman-auto.sh never ran.
    assert "preseed/include_command" not in common + auto + custom
    assert "partman/early_command string sh /cdrom/hyperlite/partman-auto.sh" in auto
    # The automatic mode asks one confirmation, the one that writes the disk and names it.
    assert "d-i partman-lvm/confirm" not in auto
    # No network mirror in either mode.
    assert "d-i apt-setup/use_mirror boolean false" in common
    assert "hyperlite-questions.sh ask" in common and "hyperlite-questions.sh keyboard" in common


def test_corosync_is_a_dependency_kept_stopped_until_a_cluster_configures_it():
    """The cluster is created from the dashboard, which asked to "apt install corosync" by hand on a node installed
    from the package; Debian's own sample configuration (no encryption) must not run meanwhile."""
    control = (INSTALLER / "deb" / "control.template").read_text()
    depends = next(line for line in control.splitlines() if line.startswith("Depends:"))
    assert "corosync" in [d.strip().split(" ")[0] for d in depends[len("Depends:") :].split(",")]
    postinst = (INSTALLER / "deb" / "postinst").read_text()
    assert "systemctl disable --now corosync.service" in postinst and "crypto_cipher" in postinst
