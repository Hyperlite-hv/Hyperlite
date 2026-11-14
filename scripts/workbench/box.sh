#!/bin/bash
# The Terminal's box (docs/design/workbench.md): the relay to the API in the background, the tmux server in the
# foreground, and its one session made from inside the box. Attaching from outside never makes a server or a session
# (app/core/workbench_box.py, attach_argv): every shell runs here. The session's end (its shell exited) ends the server
# (tmux.conf), so the unit and the box.
set -eu
SOCK=/run/workbench/tmux.sock
CONF=/usr/lib/hyperlite/scripts/workbench/tmux.conf
python3 -I /usr/lib/hyperlite/scripts/workbench/relay.py tcp-to-unix 127.0.0.1 8443 /run/workbench/api.sock &
/usr/bin/tmux -D -S "$SOCK" -f "$CONF" &
SERVER=$!
for _ in $(seq 50); do [ -S "$SOCK" ] && break; sleep 0.1; done
/usr/bin/tmux -S "$SOCK" new-session -d -s main
wait "$SERVER"
