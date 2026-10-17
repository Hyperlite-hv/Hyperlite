#!/bin/bash
# ExecStartPre of hyperlite.service: regenerates /etc/issue (the banner shown on
# the PHYSICAL console before even logging in) and /etc/motd (shown after an SSH
# login) at every start, with the current IP: a DHCP address can change from one
# boot to the next, unlike a fixed message written once at installation time.
#
# Functional equivalent of the last screen of the Proxmox VE installer ("Please
# point your browser to https://IP:8006"), but kept up to date at every restart
# instead of being shown once during the installation.
set -e

# hostname -I lists IPv4 AND IPv6 in no guaranteed order: an IPv4 address
# (x.x.x.x pattern) is filtered explicitly rather than taking the first field,
# which can be an IPv6 (seen in testing: a link-local fec0::... shown instead of
# the IPv4 actually useful to reach the dashboard).
#
# ExecStartPre starts as soon as network-online.target is reached, but on some
# network cards that target can be declared reached before the DHCP lease is
# really obtained (seen on real hardware: a banner saying "no IP address yet" on
# the first boot, although the network works a few seconds later). So it retries
# for 20 s before giving up, rather than failing on the very first attempt.
IP=""
for _ in $(seq 1 20); do
    IP=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
    [ -n "$IP" ] && break
    sleep 1
done
IP=${IP:-"(no IP address yet -- check 'hostname -I' once connected)"}
HOST=$(hostname)

# The physical console's banner (/etc/issue, shown by getty before anyone signs in) shows the initial password: it is
# only readable on the node itself, and a fresh node cannot be signed into without it. getty runs as root, so the
# file is root-only while it holds one: /etc/issue and /etc/motd are world-readable by default, which handed the
# admin account to every local account and service of the node. /etc/motd (after an SSH sign-in, any account) only
# says where the password is. The password is changed at the first sign-in, which removes the file
# (app/core/initial_password.py).
# Without the file, the administrator chose the password during a custom installation (installer/postinstall.sh
# removes the random one then), or changed it.
if [ -f /root/.hyperlite-initial-password ]; then
    ROOT_PASS="$(cat /root/.hyperlite-initial-password)"
    PASS_NOTE="(initial password, to change at the first sign-in)"
    MOTD_PASS="in /root/.hyperlite-initial-password (root only)"
else
    ROOT_PASS="(the one chosen at installation)"
    PASS_NOTE=""
    MOTD_PASS="$ROOT_PASS"
fi

banner() {
    cat <<BANNER

================================================================
  Hyperlite - Welcome

  Web interface  : https://${IP}:8000
  Account        : admin
  Password       : $1
                   ${PASS_NOTE}

  Host           : ${HOST}
================================================================

BANNER
}

# Written beside, then renamed: the old /etc/issue keeps its mode when written in place.
(umask 077 && banner "$ROOT_PASS" > /etc/issue.hyperlite)
[ -f /root/.hyperlite-initial-password ] || chmod 644 /etc/issue.hyperlite
mv -f /etc/issue.hyperlite /etc/issue
banner "$MOTD_PASS" > /etc/motd

# The service can start after getty has already shown its prompt (first boot after
# installation): ask running gettys to re-read /etc/issue so the banner appears.
agetty --reload 2>/dev/null || true
