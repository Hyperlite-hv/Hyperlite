#!/bin/bash
# Run by the package's postinst, on a first installation and on every upgrade (idempotent). Installs Hyperlite's qemu
# hooks in libvirt's hook directory, next to any hook of the administrator's own, which is never touched:
# - hyperlite-vlan: sets the VLANs of VMs on VLAN-aware Linux bridges (scripts/libvirt-hook-vlan, docs/design/network.md);
# - hyperlite-hookscript: runs the hook script attached to a VM (scripts/libvirt-hook-hookscript,
#   docs/design/hook-scripts.md).
# libvirt looks for its hooks only when it starts (a hook added while it runs is never called, checked on libvirt 10):
# a running libvirt is restarted when a hook was added or changed. Running VMs keep running across that restart.
set -e

log() { echo "[ensure-libvirt-hooks] $*"; }

CODE=${HYPERLITE_CODE:-/usr/lib/hyperlite}
DIR=${HYPERLITE_LIBVIRT_HOOKS:-/etc/libvirt/hooks}/qemu.d

# The hook scripts' library: root's only, as the hook refuses a script anyone else could change.
install -d -m 0755 -o root -g root "${HYPERLITE_VAR_DIR:-/var/lib/hyperlite}/snippets"

changed=
for name in vlan hookscript; do
    hook="$DIR/hyperlite-$name"
    if [ -f "$hook" ] && cmp -s "$CODE/scripts/libvirt-hook-$name" "$hook"; then
        continue
    fi
    log "libvirt hook: $hook"
    install -d -m 0755 "$DIR"
    install -m 0755 "$CODE/scripts/libvirt-hook-$name" "$hook"
    changed=1
done
[ -n "$changed" ] || exit 0
# Whichever runs the QEMU driver: the monolithic libvirtd (Debian's default) or virtqemud. A stopped daemon (an
# installation in a chroot, from the ISO) finds the hooks when it starts.
for unit in libvirtd.service virtqemud.service; do
    systemctl is-active --quiet "$unit" 2>/dev/null || continue
    if ! systemctl restart "$unit"; then
        log "WARNING: $unit did not restart: the hooks run once it has (systemctl restart $unit)"
    fi
done
