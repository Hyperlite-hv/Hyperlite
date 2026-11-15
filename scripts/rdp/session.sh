#!/bin/bash
# A remote desktop session (docs/design/console-2.md, section 8), run by hyperlite-daemon in a transient unit with its
# own dynamic user: a headless X server with a built-in VNC server on a Unix socket only (TigerVNC's Xvnc), and the
# FreeRDP client full screen in it. Every FreeRDP argument (the guest's address, the size, the account and password)
# comes from the unit's credential file, never from a command line.
set -u
sock="$RUNTIME_DIRECTORY/vnc.sock"
Xvnc :1 -nolisten tcp -rfbport -1 -rfbunixpath "$sock" -rfbunixmode 0600 -SecurityTypes None \
  -geometry "${RDP_W}x${RDP_H}" -depth 24 -fp built-ins -AlwaysShared -AcceptSetDesktopSize=0 -SendPrimary=0 &
xvnc=$!
for _ in $(seq 100); do [ -S "$sock" ] && break; sleep 0.1; done
if [ ! -S "$sock" ]; then echo "The display did not start" >&2; kill "$xvnc" 2>/dev/null; exit 3; fi
export DISPLAY=:1
xfreerdp3 /args-from:"$CREDENTIALS_DIRECTORY/rdp"
rc=$?
kill "$xvnc" 2>/dev/null
wait "$xvnc" 2>/dev/null
exit "$rc"
