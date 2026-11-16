#!/bin/bash
# A remote desktop session (docs/design/console-2.md, section 8), run by hyperlite-daemon in a transient unit with its
# own dynamic user: a headless X server with a built-in VNC server on a Unix socket only (TigerVNC's Xvnc), and the
# FreeRDP client full screen in it. Every FreeRDP argument (the guest's address, the size, the account and password)
# comes from the unit's credential file, never from a command line.
set -u
sock="$RUNTIME_DIRECTORY/vnc.sock"
# The X server listens on a file socket of the unit's own /tmp only (-nolisten local: no abstract socket, which lives in
# the host's network namespace, where every session's display :1 met and the second session's client drew on the
# first one's screen). It ends by itself ten minutes after its last viewer left.
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
Xvnc :1 -nolisten tcp -nolisten local -rfbport -1 -rfbunixpath "$sock" -rfbunixmode 0600 -SecurityTypes None \
  -MaxDisconnectionTime=600 \
  -geometry "${RDP_W}x${RDP_H}" -depth 24 -fp built-ins -AlwaysShared -AcceptSetDesktopSize=0 -SendPrimary=0 &
xvnc=$!
for _ in $(seq 100); do [ -S "$sock" ] && break; sleep 0.1; done
if [ ! -S "$sock" ]; then echo "The display did not start" >&2; kill "$xvnc" 2>/dev/null; exit 3; fi
export DISPLAY=:1
xfreerdp3 /args-from:"$CREDENTIALS_DIRECTORY/rdp" &
rdp=$!
# Whichever ends first ends the session: the guest's sign-out, or the display left without a viewer.
wait -n "$rdp" "$xvnc"
rc=$?
kill "$rdp" 2>/dev/null
kill "$xvnc" 2>/dev/null
wait "$xvnc" 2>/dev/null
exit "$rc"
