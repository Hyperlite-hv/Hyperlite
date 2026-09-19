#!/bin/bash
# ExecStartPre of hyperlite.service. Generates the self-signed TLS certificate on
# the very first start (idempotent: touches nothing if it already exists).
#
# Deliberately different from a certificate generated once and for all during the
# installation (chroot): at that point the machine has no real DHCP address on
# the final network yet, so there is no way to know which IP to put in the
# certificate. It is therefore generated here, on the first real boot, once the
# IP is known.
set -e

TLS_DIR=/root/hyperlite/data/tls
KEY="$TLS_DIR/hyperlite.key"
CRT="$TLS_DIR/hyperlite.crt"
CNF="$TLS_DIR/openssl-hyperlite.cnf"

[ -f "$KEY" ] && [ -f "$CRT" ] && exit 0

mkdir -p "$TLS_DIR"
chmod 700 "$TLS_DIR"

HOSTNAME_FQDN=$(hostname -f 2>/dev/null || hostname)
# First non-loopback IPv4 found: enough for a convenience self-signed certificate
# (the user has to accept the browser warning anyway, as on Proxmox), no need to
# list every IP. hostname -I does not guarantee IPv4/IPv6 order, so an IPv4
# address (x.x.x.x pattern) is filtered explicitly; otherwise the certificate may
# end up with an IPv6 in its SAN while the user connects over IPv4 (seen in
# testing). The same DHCP race as with write-motd.sh is possible
# (network-online.target can be reached before the real DHCP lease), hence the
# same retry loop before falling back to 127.0.0.1.
IP=""
for _ in $(seq 1 20); do
    IP=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
    [ -n "$IP" ] && break
    sleep 1
done
IP=${IP:-127.0.0.1}

cat > "$CNF" <<CNFEOF
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no

[dn]
CN = $HOSTNAME_FQDN

[v3_req]
subjectAltName = @alt_names
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
DNS.1 = $HOSTNAME_FQDN
DNS.2 = localhost
IP.1 = $IP
IP.2 = 127.0.0.1
CNFEOF

openssl req -x509 -nodes -newkey rsa:2048 \
    -keyout "$KEY" -out "$CRT" -days 3650 \
    -config "$CNF" -extensions v3_req >/dev/null 2>&1

chmod 600 "$KEY"
chmod 644 "$CRT"
