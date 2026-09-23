"""Conservative installation defaults, not an OS certification database."""

import re

from app.core.unattended_install import detect_windows


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
