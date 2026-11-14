#!/bin/sh
# The Terminal's box (docs/design/workbench.md): the relay to the API in the background, then the tmux server in the
# foreground, whose end is the session's end.
set -eu
python3 -I /usr/lib/hyperlite/scripts/workbench/relay.py tcp-to-unix 127.0.0.1 8443 /run/workbench/api.sock &
exec /usr/bin/tmux -D -S /run/workbench/tmux.sock -f /usr/lib/hyperlite/scripts/workbench/tmux.conf
