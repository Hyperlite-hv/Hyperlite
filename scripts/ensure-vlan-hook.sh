#!/bin/bash
# Run by the package's postinst, on a first installation and on every upgrade (idempotent). Installs libvirt's qemu hook
# that sets the VLANs of VMs on VLAN-aware Linux bridges (scripts/libvirt-hook-vlan, docs/design/network.md), in
# libvirt's hook directory, next to any hook of the administrator's own, which is never touched.
# libvirt looks for its hooks only when it starts (a hook added while it runs is never called, checked on libvirt 10):
# a running libvirt is restarted when the hook was added or changed. Running VMs keep running across that restart.
set -e

log() { echo "[ensure-vlan-hook] $*"; }

CODE=${HYPERLITE_CODE:-/usr/lib/hyperlite}
DIR=${HYPERLITE_LIBVIRT_HOOKS:-/etc/libvirt/hooks}/qemu.d
HOOK="$DIR/hyperlite-vlan"

if [ -f "$HOOK" ] && cmp -s "$CODE/scripts/libvirt-hook-vlan" "$HOOK"; then
    exit 0
fi
log "libvirt hook for VM VLANs: $HOOK"
install -d -m 0755 "$DIR"
install -m 0755 "$CODE/scripts/libvirt-hook-vlan" "$HOOK"
# Whichever runs the QEMU driver: the monolithic libvirtd (Debian's default) or virtqemud. A stopped daemon (an
# installation in a chroot, from the ISO) finds the hook when it starts.
for unit in libvirtd.service virtqemud.service; do
    systemctl is-active --quiet "$unit" 2>/dev/null || continue
    if ! systemctl restart "$unit"; then
        log "WARNING: $unit did not restart: VM VLANs are set at start once it has (systemctl restart $unit)"
    fi
done
