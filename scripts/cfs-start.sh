#!/bin/sh
# ExecStart of hyperlite-cfs.service (docs/design/cfs-files.md, sections 6 and 15): the tree is mounted at
# /etc/hyperlite once the node's files have moved into it, which left the directory empty; at /etc/hyperlite/cfs while
# it still holds them (a node not moved yet: the move runs at the API's next start, app/core/node_files.py). Never over
# a directory that holds files. A mount a crash left behind ("Transport endpoint is not connected") is cleaned first.
ETC=/etc/hyperlite

fusermount3 -u -z "$ETC" 2>/dev/null || true
fusermount3 -u -z "$ETC/cfs" 2>/dev/null || true
mkdir -p "$ETC"
if [ -z "$(ls -A "$ETC" 2>/dev/null | grep -vx cfs)" ] && [ -z "$(ls -A "$ETC/cfs" 2>/dev/null)" ]; then
    rmdir "$ETC/cfs" 2>/dev/null || true
    MOUNT="$ETC"
else
    mkdir -p "$ETC/cfs"
    MOUNT="$ETC/cfs"
fi
# shellcheck disable=SC2086 # HYPERLITE_CFS_MODE is empty or one flag
exec /usr/lib/hyperlite/hyperlite-cfs $HYPERLITE_CFS_MODE --db /var/lib/hyperlite-cfs/config.db \
    --socket /run/hyperlite-cfs/socket --socket-mode 0660 --socket-group www-data --mount "$MOUNT"
