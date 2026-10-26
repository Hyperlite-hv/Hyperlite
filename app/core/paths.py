"""Where Hyperlite keeps its files: from 1.1 where Proxmox VE keeps its own (docs/design/privilege-separation.md,
section 9), every path in one place. Up to 1.0 everything lived under /root/hyperlite; scripts/migrate_layout.sh moves
a node's files from there, and holds the same list.

  code               /usr/lib/hyperlite                 (Proxmox: /usr/share/perl5/PVE)
  web interface      /usr/share/hyperlite/www           (Proxmox: /usr/share/pve-manager)
  node secrets       /etc/hyperlite/priv                (Proxmox: /etc/pve/priv)
  node TLS           /etc/hyperlite/local               (Proxmox: /etc/pve/local)
  data               /var/lib/hyperlite                 (Proxmox: /var/lib/vz, /var/lib/pve-cluster)

HYPERLITE_VAR_DIR and HYPERLITE_ETC_DIR move the data and the configuration elsewhere (the test suite, the e2e
backend, a development checkout); HYPERLITE_WWW_DIR the web interface."""

import os
from pathlib import Path

# The code: /usr/lib/hyperlite once installed, the checkout in development.
CODE = Path(__file__).resolve().parents[2]
INSTALLED_CODE = Path("/usr/lib/hyperlite")

VAR = Path(os.environ.get("HYPERLITE_VAR_DIR") or "/var/lib/hyperlite")
ETC = Path(os.environ.get("HYPERLITE_ETC_DIR") or "/etc/hyperlite")
# The package ships the built interface apart from the code, as Proxmox does; a checkout builds it in place.
WWW = Path(
    os.environ.get("HYPERLITE_WWW_DIR")
    or ("/usr/share/hyperlite/www" if CODE == INSTALLED_CODE else CODE / "dashboard" / "dist")
)

# /etc/hyperlite: root only (0700), as /etc/pve/priv.
PRIV = ETC / "priv"
ENV_FILE = PRIV / "env"  # the node's keys (session signing, encryption); a systemd EnvironmentFile
SSH_DIR = PRIV / "ssh"  # the key this node uses to reach the others and its VMs
INITIAL_PASSWORD_FILE = PRIV / "initial-admin-password.txt"
TLS_DIR = ETC / "local"  # the web interface's certificate and key

# /run/hyperlite: what lasts as long as the services (hyperlite-daemon's socket, its tunnels to the other nodes).
RUN = Path(os.environ.get("HYPERLITE_RUN_DIR") or "/run/hyperlite")
# /run/lock/hyperlite: the claims on VMs (app/core/vm_locks.py), shared by every process that works on a VM (the API,
# the schedulers), as Proxmox VE locks a guest with /run/lock/qemu-server/lock-<vmid>.conf. Not under /run/hyperlite,
# which is hyperlite-daemon's and removed when it stops: a claim's file must outlive any one service.
LOCKS = Path(os.environ.get("HYPERLITE_LOCK_DIR") or "/run/lock/hyperlite")

# /var/lib/hyperlite
DB = VAR / "hyperlite.db"
ISOS = VAR / "template" / "iso"
TEMPLATES = VAR / "template" / "vm"
BACKUPS = VAR / "dump"
EXPORTS = BACKUPS / "export"
CONTAINER_BACKUPS = BACKUPS / "container"
IMPORTED_DISKS = VAR / "import"
SNIPPETS = VAR / "snippets"  # hook scripts, placed by root only (app/core/hookscript.py)
HOOKSCRIPT_RUNS = VAR / "hookscript-runs"
K3S = VAR / "k3s"
BIN = VAR / "bin"
TMP = VAR / "tmp"  # the service's TMPDIR: uploads of several GB, on the data disk rather than a tmpfs /tmp
CONFIG_COPY_STAGING = VAR / "config-copy-staging"  # a copy this node is preparing to send
UPDATE_BACKUPS = VAR / "update-backups"
PREVIOUS = VAR / "previous"  # the package of the version an update replaced, for a rollback
UPDATE = VAR / "update"  # the update watchdog's copy and its log, out of every directory a version may move

# The node's libraries, written by hyperlite-daemon and read by the API, which as www-data may not make them: made at
# the daemon's start (make_libraries), before the API's.
LIBRARIES = (ISOS, TEMPLATES, EXPORTS, IMPORTED_DISKS)


def make_libraries():
    for directory in LIBRARIES:
        directory.mkdir(parents=True, exist_ok=True)


# Where versions up to 1.0 kept what a database or the cluster's configuration may still name: a backup's directory, a
# backup job's (each schedule stored the default directory in full). Those rows are left as they are, since nodes still
# on 1.0 read them during a rolling update; this version reads them at the new place.
LEGACY = (
    ("/root/hyperlite/data/backups", BACKUPS),
    ("/root/hyperlite/data/vm-exports", EXPORTS),
    ("/root/hyperlite-container-backups", CONTAINER_BACKUPS),
)


def current(path):
    """`path` where this version keeps it: one under a directory of the layout up to 1.0 is translated, any other is
    returned as it is (a string; None or empty as given)."""
    if not path:
        return path
    text = str(path)
    for old, new in LEGACY:
        if text == old or text.startswith(old + "/"):
            return str(new) + text[len(old) :]
    return text
