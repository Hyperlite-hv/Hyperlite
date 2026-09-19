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

# The password is WRITTEN IN CLEAR TEXT here rather than pointing to
# /root/.hyperlite-initial-password: that file is only readable once logged in as
# root, which is a chicken-and-egg problem (you cannot log in without the
# password, and cannot read the password without being logged in). /etc/issue is
# already shown BEFORE authentication on the physical console: anyone with
# physical access to the screen has already crossed the same trust barrier as
# root access, so writing it here exposes it no further.
ROOT_PASS=$(cat /root/.hyperlite-initial-password 2>/dev/null || echo "(see /root/.hyperlite-initial-password)")

BANNER="
================================================================
  Hyperlite - Welcome

  Web interface  : https://${IP}:8000
  Account        : admin
  Password       : ${ROOT_PASS}
                   (initial password, change it after signing in)

  Host           : ${HOST}
================================================================
"

echo "$BANNER" > /etc/issue
echo "$BANNER" > /etc/motd
