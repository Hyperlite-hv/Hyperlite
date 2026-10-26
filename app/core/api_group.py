"""The API's group (docs/design/privilege-separation.md, section 14.6): hyperlite.service runs as www-data from 4e, as
Proxmox VE's pveproxy does. What root's services make and the API reads or writes is given that group, as
/etc/pve/local/pveproxy-ssl.key is root:www-data 0640; what holds root's secrets keeps root's alone."""

import contextlib
import grp
import os

NAME = "www-data"


def gid():
    """The group's id, or None on a system without it (a developer's machine)."""
    try:
        return grp.getgrnam(NAME).gr_gid
    except KeyError:
        return None


def share(path, mode):
    """`path` given `mode`, and the API's group when this process may change it (root). Its owner stays."""
    os.chmod(path, mode)
    group = gid()
    if group is not None and os.geteuid() == 0:
        with contextlib.suppress(OSError):
            os.chown(path, -1, group)


def share_fd(fd, mode):
    """share() for a file this process holds open: no window where the path names another file."""
    os.fchmod(fd, mode)
    group = gid()
    if group is not None and os.geteuid() == 0:
        with contextlib.suppress(OSError):
            os.fchown(fd, -1, group)
