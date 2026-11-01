#!/bin/sh
# ExecStartPost of hyperlite-cfs.service: the units after it (the API, hyperlite-daemon...) read the node's files from
# the mount, so it is ready only once its socket answers and the tree is mounted. A mount that does not come is logged
# and the socket served all the same (the files are a view; a node not moved yet reads its own from the disk).
for _ in $(seq 1 300); do
    if [ -S /run/hyperlite-cfs/socket ] && { mountpoint -q /etc/hyperlite || mountpoint -q /etc/hyperlite/cfs; }; then
        exit 0
    fi
    sleep 0.1
done
echo "hyperlite-cfs: the tree is not mounted after 30 s" >&2
exit 0
