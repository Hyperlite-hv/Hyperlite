"""Conservative installation defaults, not an OS certification database."""

import re


def detect_windows(iso_filename):
    """Return whether an ISO is a Windows installer handled with native SATA devices."""
    if not iso_filename:
        return False
    name = iso_filename.lower()
    return bool(
        re.search(r"windows|(?:^|[^a-z0-9])(?:win|server)[-_ ]?(?:10|11|2016|2019|2022|2025)(?:[^0-9]|$)", name)
    )


def guest_profile(iso_filename, requested="auto"):
    # Existing disk imports and the built-in cloud image retain their defaults.
    if not iso_filename:
        return "linux"
    if requested != "auto":
        return requested
    if detect_windows(iso_filename):
        return "windows"
    if re.search(r"ubuntu|debian|rhel|centos|rocky|alma|fedora|opensuse|sles|archlinux|alpine", iso_filename.lower()):
        return "linux"
    return "other"
