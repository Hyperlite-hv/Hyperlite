#!/bin/bash
# Run by the package's postinst, on a first installation and on every upgrade (idempotent). Lets QEMU bind the NBD
# export of a running VM's backup into a backup store (app/core/backup_store_vm.py, docs/design/backup-store.md), and
# the sockets of a live migration between clusters (app/core/site_live.py, docs/design/multi-cluster.md, 9.2):
# libvirt adds a backup's scratch files to the VM's AppArmor profile, not the export's socket, and QEMU's bind fails
# with "Permission denied". One line in AppArmor's local include for QEMU, between markers; anything else of the
# administrator's own in that file is never touched.
# The profiles of VMs already running were compiled at their start: each is loaded again, so a backup of a VM started
# before this installation works too. Running VMs keep running.
set -e

log() { echo "[ensure-backup-apparmor] $*"; }

LOCAL=${HYPERLITE_APPARMOR_LOCAL:-/etc/apparmor.d/local/abstractions/libvirt-qemu}
PROFILES=${HYPERLITE_APPARMOR_LIBVIRT:-/etc/apparmor.d/libvirt}
BEGIN="# BEGIN hyperlite backup store"
END="# END hyperlite backup store"
# The backups' exports, and a live migration between clusters' sockets (app/core/site_live.py: QEMU binds the target's
# and opens the source's, in a directory of their own per migration).
RULES="  /run/hyperlite/backup/*.sock rw,
  /run/hyperlite/migration/*/*.sock rw,"

[ -d "$(dirname "$(dirname "$(dirname "$LOCAL")")")" ] || exit 0 # no /etc/apparmor.d: nothing to allow
wanted=$(printf '%s\n%s\n%s\n' "$BEGIN" "$RULES" "$END")
if [ -f "$LOCAL" ] && awk -v b="$BEGIN" -v e="$END" '$0==b{f=1} f{print} $0==e{f=0}' "$LOCAL" | cmp -s - <(echo "$wanted"); then
    exit 0
fi
log "QEMU may bind a backup's export: $LOCAL"
install -d -m 0755 "$(dirname "$LOCAL")"
touch "$LOCAL"
tmp=$(mktemp "$LOCAL.XXXXXX")
awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$LOCAL" > "$tmp"
echo "$wanted" >> "$tmp"
chmod 0644 "$tmp"
mv "$tmp" "$LOCAL"
command -v apparmor_parser > /dev/null || exit 0
for profile in "$PROFILES"/libvirt-*; do
    case "$profile" in *.files | *'*') continue ;; esac
    if ! apparmor_parser -r "$profile" 2> /dev/null; then
        log "WARNING: $profile not reloaded: a backup of that VM works once it has restarted"
    fi
done
