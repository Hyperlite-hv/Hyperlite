#!/bin/bash
# Run by the package's postinst, on a first installation and on every upgrade (idempotent). Prepares this node for
# SPICE consoles (docs/design/spice-console.md): QEMU serves SPICE in TLS only, behind Hyperlite's proxy, so a .vv file
# carries this node's CA and certificate subject and the client checks it is talking to this node's QEMU.
#   - A CA and a server certificate in libvirt's own SPICE directory (Debian's AppArmor profile lets QEMU read it).
#     The CA's key is deleted once the server certificate is signed: nothing else is ever signed with it.
#   - spice_tls in /etc/libvirt/qemu.conf (libvirt 10 reads no drop-in directory for it); libvirt is restarted only
#     when this changed the file. Running VMs keep running across that restart; the setting applies to VMs started
#     afterwards.
# An administrator's own spice_tls setting, or certificates already in the directory, are left as they are.
set -e

log() { echo "[ensure-spice-tls] $*"; }

DIR=${HYPERLITE_SPICE_TLS_DIR:-/etc/pki/libvirt-spice}
QEMU_CONF=${HYPERLITE_QEMU_CONF:-/etc/libvirt/qemu.conf}
MARK="# Hyperlite: SPICE consoles in TLS only (scripts/ensure-spice-tls.sh)"

if [ ! -f "$DIR/ca-cert.pem" ] || [ ! -f "$DIR/server-cert.pem" ] || [ ! -f "$DIR/server-key.pem" ]; then
    log "SPICE certificates for this node"
    install -d -m 755 "$DIR"
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    node=$(hostname -s 2>/dev/null || hostname)
    openssl req -x509 -nodes -newkey rsa:3072 -days 3650 -keyout "$work/ca-key.pem" -out "$work/ca-cert.pem" \
        -subj "/O=Hyperlite/CN=Hyperlite SPICE CA $node" >/dev/null 2>&1
    openssl req -nodes -newkey rsa:3072 -keyout "$work/server-key.pem" -out "$work/server.csr" \
        -subj "/O=Hyperlite/CN=$node" >/dev/null 2>&1
    printf 'basicConstraints = CA:FALSE\nkeyUsage = digitalSignature, keyEncipherment\nextendedKeyUsage = serverAuth\n' \
        > "$work/ext.cnf"
    openssl x509 -req -days 3650 -in "$work/server.csr" -CA "$work/ca-cert.pem" -CAkey "$work/ca-key.pem" \
        -CAcreateserial -extfile "$work/ext.cnf" -out "$work/server-cert.pem" >/dev/null 2>&1
    # QEMU reads the key as the user it runs as (qemu.conf's "user", libvirt-qemu on Debian): that user only.
    qemu_user=$(sed -n 's/^[[:space:]]*user[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$QEMU_CONF" 2>/dev/null | tail -1)
    qemu_user=${qemu_user:-libvirt-qemu}
    id -u "$qemu_user" >/dev/null 2>&1 || qemu_user=root
    install -m 644 "$work/ca-cert.pem" "$DIR/ca-cert.pem"
    install -m 644 "$work/server-cert.pem" "$DIR/server-cert.pem"
    install -m 400 -o "$qemu_user" "$work/server-key.pem" "$DIR/server-key.pem"
fi

if grep -Eq '^[[:space:]]*spice_tls[[:space:]]*=' "$QEMU_CONF" 2>/dev/null; then
    exit 0  # set before, by this script or by an administrator: theirs to keep
fi
if [ ! -f "$QEMU_CONF" ]; then
    log "WARNING: $QEMU_CONF not found: SPICE consoles stay unavailable on this node"
    exit 0
fi
log "SPICE in TLS in $QEMU_CONF"
printf '\n%s\nspice_tls = 1\nspice_tls_x509_cert_dir = "%s"\n' "$MARK" "$DIR" >> "$QEMU_CONF"
# Whichever runs the QEMU driver: the monolithic libvirtd (Debian's default) or virtqemud. Only a running one is
# restarted: a stopped daemon (an installation in a chroot, from the ISO) reads the file when it starts, and a unit
# this node does not have is not an error.
for unit in libvirtd.service virtqemud.service; do
    systemctl is-active --quiet "$unit" 2>/dev/null || continue
    if ! systemctl restart "$unit"; then
        log "WARNING: $unit did not restart: SPICE consoles work once it has (systemctl restart $unit)"
    fi
done
