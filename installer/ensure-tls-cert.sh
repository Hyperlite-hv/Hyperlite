#!/bin/bash
# ExecStartPre de hyperlite.service. Genere le certificat TLS auto-signe au
# tout premier demarrage (idempotent : ne touche a rien s'il existe deja).
#
# Difference volontaire avec un cert genere une fois pour toutes pendant
# l'installation (chroot) : a ce stade-la, la machine n'a pas encore
# d'adresse DHCP reelle sur le reseau final -- on ne peut pas savoir quelle
# IP mettre dans le certificat. On le genere donc ici, au premier vrai boot,
# une fois l'IP connue.
set -e

TLS_DIR=/root/hyperlite/data/tls
KEY="$TLS_DIR/hyperlite.key"
CRT="$TLS_DIR/hyperlite.crt"
CNF="$TLS_DIR/openssl-hyperlite.cnf"

[ -f "$KEY" ] && [ -f "$CRT" ] && exit 0

mkdir -p "$TLS_DIR"
chmod 700 "$TLS_DIR"

HOSTNAME_FQDN=$(hostname -f 2>/dev/null || hostname)
# Premiere IPv4 non-loopback trouvee -- suffisant pour un certificat auto-signe
# de confort (l'utilisateur devra de toute facon accepter l'avertissement
# navigateur, comme sur Proxmox) ; pas la peine de lister toutes les IP.
# hostname -I ne garantit pas l'ordre IPv4/IPv6 -- filtre explicitement une
# adresse IPv4 (motif x.x.x.x), sinon le certificat peut finir avec une IPv6
# dans son SAN alors que l'utilisateur se connecte en IPv4 (observe en test).
# Meme course DHCP possible qu'avec write-motd.sh (network-online.target
# peut se declarer atteint avant le bail DHCP reel) -- meme boucle de
# tentatives avant d'abandonner sur 127.0.0.1.
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
