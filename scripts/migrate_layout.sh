#!/bin/bash
# Moves Hyperlite's files between the layout up to 1.0 (/root/hyperlite...) and the one of 1.1 and later, modelled on
# Proxmox VE's (docs/design/privilege-separation.md, section 9):
#
#   ISO images        data/isos            -> /var/lib/hyperlite/template/iso      (Proxmox: /var/lib/vz/template/iso)
#   VM templates      data/templates       -> /var/lib/hyperlite/template/vm
#   backups           data/backups         -> /var/lib/hyperlite/dump              (Proxmox: /var/lib/vz/dump)
#   VM exports        data/vm-exports      -> /var/lib/hyperlite/dump/export
#   container backups /root/hyperlite-container-backups -> /var/lib/hyperlite/dump/container
#   disks to import   data/imported-disks  -> /var/lib/hyperlite/import            (Proxmox: /var/lib/vz/import)
#   node TLS          data/tls             -> /etc/hyperlite/local                 (Proxmox: /etc/pve/local/pve-ssl.*)
#   node secrets      .env, data/ssh, data/initial-admin-password.txt -> /etc/hyperlite/priv  (Proxmox: /etc/pve/priv)
#   the rest          database, k3s, bin, tmp, update backups -> /var/lib/hyperlite; the configuration copy
#                     staged here goes to config-copy-staging (config-copy is where a node RECEIVES one: never touched)
#
#   migrate_layout.sh forward   to the new layout (1.1, at its first start)
#   migrate_layout.sh back      to the old one (1.0.2, when a 1.1 that failed is rolled back to it)
#
# Run with the service stopped (the database is moved). Idempotent: an item already in place, or absent, is left
# alone. All or nothing: on a failure the items moved by this run go back where they were, and it exits 1. It never
# overwrites: an item present on both sides stops it before anything moves, except an empty directory, which is only
# removed. The moves are renames, instant on one file system whatever the size; across file systems mv copies.
# Whatever else data/ holds moves along as /var/lib/hyperlite/legacy-data, so nothing is left behind or lost.
#
# HYPERLITE_LAYOUT_ROOT prefixes every path (tests).
set -u

R="${HYPERLITE_LAYOUT_ROOT:-}"
O="$R/root/hyperlite"
N="$R/var/lib/hyperlite"
E="$R/etc/hyperlite"
# Pairs in move order: a directory moved into another one comes after it (dump, then dump/export).
OLD=(
    "$O/hyperlite.db" "$O/hyperlite.db-wal" "$O/hyperlite.db-shm"
    "$O/data/isos" "$O/data/templates" "$O/data/backups" "$O/data/vm-exports" "$R/root/hyperlite-container-backups"
    "$O/data/imported-disks" "$O/data/k3s" "$O/data/bin" "$O/data/config-copy" "$O/data/tmp"
    "$O/data/tls" "$O/.env" "$O/data/ssh" "$O/data/initial-admin-password.txt"
    "$R/root/hyperlite-backups"
    "$O/data"
)
NEW=(
    "$N/hyperlite.db" "$N/hyperlite.db-wal" "$N/hyperlite.db-shm"
    "$N/template/iso" "$N/template/vm" "$N/dump" "$N/dump/export" "$N/dump/container"
    "$N/import" "$N/k3s" "$N/bin" "$N/config-copy-staging" "$N/tmp"
    "$E/local" "$E/priv/env" "$E/priv/ssh" "$E/priv/initial-admin-password.txt"
    "$N/update-backups"
    "$N/legacy-data"
)

log() { echo "[migrate-layout] $*"; }
exists() { [ -e "$1" ] || [ -L "$1" ]; }
empty_dir() { [ -d "$1" ] && [ ! -L "$1" ] && [ -z "$(ls -A -- "$1")" ]; }

case "${1:-}" in
    forward)
        FROM=("${OLD[@]}"); TO=("${NEW[@]}")
        # The cache of the Ubuntu automatic installation, removed in 1.0: nothing reads it any more.
        rm -rf -- "$O/data/casper-cache"
        ;;
    back)
        # Reverse order: dump/export goes back out before dump does.
        FROM=(); TO=()
        for ((i = ${#NEW[@]} - 1; i >= 0; i--)); do FROM+=("${NEW[$i]}"); TO+=("${OLD[$i]}"); done
        ;;
    *) echo "usage: migrate_layout.sh forward|back" >&2; exit 2 ;;
esac

# The catch-all comes last: it only holds what no pair named. Empty, it is just removed.
LAST=$((${#FROM[@]} - 1))
[ "$1" = forward ] && LAST_IS_REST=1 || LAST_IS_REST=0

# Nothing moves if anything is on both sides (which copy is the right one is for a person to say), unless one side is
# an empty directory: it is only in the way, it holds nothing.
for i in "${!FROM[@]}"; do
    if exists "${FROM[$i]}" && exists "${TO[$i]}" && ! empty_dir "${FROM[$i]}" && ! empty_dir "${TO[$i]}"; then
        if [ "$LAST_IS_REST" = 1 ] && [ "$i" = "$LAST" ]; then
            continue  # data/ still holds the items the pairs move first: decided after them
        fi
        log "ERROR: both ${FROM[$i]} and ${TO[$i]} exist: nothing moved, decide which one to keep"
        exit 1
    fi
done

MOVED=()
CREATED=()  # directories this run made, removed again by undo when they end up empty
make_parent() {
    local d missing=()
    d="$(dirname -- "$1")"
    while [ ! -d "$d" ]; do missing=("$d" "${missing[@]}"); d="$(dirname -- "$d")"; done
    mkdir -p -- "$(dirname -- "$1")" || return 1
    CREATED+=("${missing[@]}")
}
undo() {
    for ((j = ${#MOVED[@]} - 1; j >= 0; j--)); do
        k="${MOVED[$j]}"
        mkdir -p -- "$(dirname -- "${FROM[$k]}")"
        mv -T -- "${TO[$k]}" "${FROM[$k]}" && log "put back ${FROM[$k]}" || log "ERROR: could not put back ${FROM[$k]}"
    done
    for ((j = ${#CREATED[@]} - 1; j >= 0; j--)); do
        rmdir -- "${CREATED[$j]}" 2>/dev/null || true
    done
}

for i in "${!FROM[@]}"; do
    exists "${FROM[$i]}" || continue
    if empty_dir "${FROM[$i]}" && exists "${TO[$i]}"; then
        rmdir -- "${FROM[$i]}"
        continue
    fi
    if empty_dir "${TO[$i]}"; then
        rmdir -- "${TO[$i]}"
    fi
    if [ "$LAST_IS_REST" = 1 ] && [ "$i" = "$LAST" ] && empty_dir "${FROM[$i]}"; then
        rmdir -- "${FROM[$i]}"
        continue
    fi
    if ! make_parent "${TO[$i]}" || ! mv -T -- "${FROM[$i]}" "${TO[$i]}"; then
        log "ERROR: could not move ${FROM[$i]} to ${TO[$i]}: everything moved by this run goes back"
        undo
        exit 1
    fi
    MOVED+=("$i")
    log "moved ${FROM[$i]} to ${TO[$i]}"
done

# Root's only, whichever way the files went, as /etc/pve/priv.
for d in "$E/priv" "$E/priv/ssh" "$E/local"; do
    [ -d "$d" ] && chmod 700 "$d"
done
[ "${#MOVED[@]}" -eq 0 ] && log "nothing to move ($1)"
exit 0
