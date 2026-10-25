#!/bin/bash
# Moves Hyperlite's files between the layout up to 1.0 (/root/hyperlite...) and the one of 1.1 and later, modelled on
# Proxmox VE's (app/core/paths.py, docs/design/privilege-separation.md, section 9):
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
#   migrate_layout.sh forward   to the new layout (1.1, at every start of the service: ExecStartPre)
#   migrate_layout.sh back      to the old one (what 1.0.2 does when a 1.1 that failed is rolled back to it)
#
# Run with the service stopped (the database is moved). Idempotent: an item already in place, or absent, is left
# alone. All or nothing: on a failure the items moved by this run go back where they were, and it exits 1. It never
# overwrites: an item present on both sides stops it before anything moves, except an empty directory, which is only
# removed. The moves are renames, instant on one file system whatever the size; across file systems mv copies.
# Whatever else data/ holds moves along as /var/lib/hyperlite/legacy-data, so nothing is left behind or lost.
#
# Once a node is on the new layout (its database moved), forward only finishes what is left: an item found on both
# sides then is left where it is, with a warning, rather than keeping the service from starting (a migration from a
# node still on 1.0 copies a VM's ISO image to the same path here, under /root/hyperlite). After the moves, forward
# points the VMs' ISO images at their new place and removes what the old installation left of /root/hyperlite.
#
# HYPERLITE_LAYOUT_ROOT prefixes every path (tests); HYPERLITE_LAYOUT_VIRSH replaces virsh (tests: with
# HYPERLITE_LAYOUT_ROOT set and no HYPERLITE_LAYOUT_VIRSH, the host's libvirt is never touched).
set -u

R="${HYPERLITE_LAYOUT_ROOT:-}"
O="$R/root/hyperlite"
N="$R/var/lib/hyperlite"
E="$R/etc/hyperlite"
# Pairs in move order: a directory moved into another one comes after it (dump, then dump/export).
OLD=(
    "$O/hyperlite.db" "$O/hyperlite.db-wal" "$O/hyperlite.db-shm" "$O/hyperlite.db.before-v1"
    "$O/data/isos" "$O/data/templates" "$O/data/backups" "$O/data/vm-exports" "$R/root/hyperlite-container-backups"
    "$O/data/imported-disks" "$O/data/k3s" "$O/data/bin" "$O/data/config-copy" "$O/data/tmp"
    "$O/data/tls" "$O/.env" "$O/data/ssh" "$O/data/initial-admin-password.txt"
    "$R/root/hyperlite-backups"
    "$O/data"
)
NEW=(
    "$N/hyperlite.db" "$N/hyperlite.db-wal" "$N/hyperlite.db-shm" "$N/hyperlite.db.before-v1"
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
# Already on the new layout: what is found on both sides is not a reason to keep the service from starting.
DONE=0
if [ "$1" = forward ] && exists "$N/hyperlite.db" && ! exists "$O/hyperlite.db"; then
    DONE=1
fi

# Nothing moves if anything is on both sides (which copy is the right one is for a person to say), unless one side is
# an empty directory: it is only in the way, it holds nothing.
SKIP=()
for i in "${!FROM[@]}"; do
    if exists "${FROM[$i]}" && exists "${TO[$i]}" && ! empty_dir "${FROM[$i]}" && ! empty_dir "${TO[$i]}"; then
        if [ "$LAST_IS_REST" = 1 ] && [ "$i" = "$LAST" ]; then
            continue  # data/ still holds the items the pairs move first: decided after them
        fi
        if [ "$DONE" = 1 ]; then
            log "WARNING: both ${FROM[$i]} and ${TO[$i]} exist: ${FROM[$i]} left where it is"
            SKIP[$i]=1
            continue
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
    [ -z "${SKIP[$i]:-}" ] || continue
    if empty_dir "${FROM[$i]}" && exists "${TO[$i]}"; then
        rmdir -- "${FROM[$i]}"
        continue
    fi
    if [ "$LAST_IS_REST" = 1 ] && [ "$i" = "$LAST" ]; then
        if empty_dir "${FROM[$i]}"; then
            rmdir -- "${FROM[$i]}"
            continue
        fi
        # An item left above may be read where it is (a VM's ISO image): data/ then stays too.
        if [ "$DONE" = 1 ] && { [ "${#SKIP[@]}" -gt 0 ] || { exists "${TO[$i]}" && ! empty_dir "${TO[$i]}"; }; }; then
            log "WARNING: ${FROM[$i]} still holds $(ls -A -- "${FROM[$i]}" | tr '\n' ' ')- left where it is"
            continue
        fi
    fi
    if empty_dir "${TO[$i]}"; then
        rmdir -- "${TO[$i]}"
    fi
    if ! make_parent "${TO[$i]}" || ! mv -T -- "${FROM[$i]}" "${TO[$i]}"; then
        log "ERROR: could not move ${FROM[$i]} to ${TO[$i]}: everything moved by this run goes back"
        undo
        exit 1
    fi
    MOVED+=("$i")
    log "moved ${FROM[$i]} to ${TO[$i]}"
done

# A VM with an ISO image of the library inserted names it by its path, in its libvirt definition: the definition follows
# the image when the image did move (one still at the old path, a copy a migration brought there, is left alone). A
# running VM keeps the file it opened; the change applies at its next start.
relocate_vms() { # <old directory> <new directory>
    local virsh="${HYPERLITE_LAYOUT_VIRSH:-}" name xml path moved tmp changed
    if [ -z "$virsh" ]; then
        if [ -n "$R" ] || ! command -v virsh >/dev/null 2>&1; then return 0; fi
        virsh=virsh
    fi
    tmp="$(mktemp)" || return 0
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        # --security-info: a definition read without it and written back would lose the console's password.
        xml="$("$virsh" dumpxml --inactive --security-info -- "$name" 2>/dev/null)" || continue
        changed=0
        while IFS= read -r path; do
            moved="$2/${path#"$1"/}"
            if [ -n "$path" ] && exists "$moved" && ! exists "$path"; then
                xml="${xml//"'$path'"/"'$moved'"}"
                changed=1
            fi
        done < <(printf '%s\n' "$xml" | grep -o "'$1/[^']*'" | tr -d "'" | sort -u)
        [ "$changed" = 1 ] || continue
        if printf '%s\n' "$xml" > "$tmp" && "$virsh" define "$tmp" >/dev/null 2>&1; then
            log "VM $name: its ISO image is now read from $2"
        else
            log "WARNING: VM $name still reads its ISO image from $1: insert it again from the dashboard"
        fi
    done < <("$virsh" list --all --name 2>/dev/null)
    rm -f -- "$tmp"
}

# What the old installation leaves of /root/hyperlite once its code is gone (the package no longer ships it there):
# the Python environment and the bytecode, derived, rebuilt by any version that needs them, then the empty
# directories. Anything else stays where it is, named in the log: a file of the administrator's, or an ISO image a
# migration copied there, which a VM may read.
clean_old_root() {
    if [ ! -d "$O" ] || [ -e "$O/app/main.py" ]; then return 0; fi
    rm -rf -- "$O/venv" "$O/venv.old" "$O/venv.new" "$O/dashboard/node_modules"
    find "$O" -name __pycache__ -type d -prune -exec rm -rf -- {} + 2>/dev/null
    find "$O" -depth -type d -empty -delete 2>/dev/null
    if [ -d "$O" ]; then
        log "left in $O (not Hyperlite's): $(ls -A -- "$O" | tr '\n' ' ')"
    else
        log "$O removed"
    fi
}

# certbot keeps the path of the hook that installs a renewed certificate (app/core/tls_certs.py) in the renewal
# configuration of each domain: it follows the code.
relocate_acme_hook() { # <old code directory> <new code directory>
    local conf
    for conf in "$R"/etc/letsencrypt/renewal/*.conf; do
        [ -f "$conf" ] && grep -q "$1/scripts/acme-deploy-hook.sh" "$conf" || continue
        sed -i "s#$1/scripts/acme-deploy-hook.sh#$2/scripts/acme-deploy-hook.sh#g" "$conf" &&
            log "certbot: $(basename "$conf") renews through $2"
    done
}

if [ "$1" = forward ]; then
    relocate_vms "$O/data/isos" "$N/template/iso"
    relocate_acme_hook /root/hyperlite /usr/lib/hyperlite
    clean_old_root
fi
# Back: the directories of the new layout that the moves left empty go too (seen on a test node: template/ and
# priv/ stayed, empty). /var/lib/hyperlite itself stays: previous/, update/ and config-copy/ live there in both.
if [ "$1" = back ]; then
    for d in "$N/template" "$N/dump" "$E/priv" "$E/local" "$E"; do
        empty_dir "$d" && rmdir -- "$d"
    done
fi
# Root's only, whichever way the files went, as /etc/pve/priv: the secrets, the archives and copies that hold them.
for d in "$E/priv" "$E/priv/ssh" "$N/update-backups" "$N/config-copy-staging" "$N/legacy-data"; do
    [ -d "$d" ] && chmod 700 "$d"
done
[ -f "$N/hyperlite.db.before-v1" ] && chmod 600 "$N/hyperlite.db.before-v1"
# Root's and the API's group (www-data from 4e, as Proxmox VE's pveproxy; docs/design/privilege-separation.md, section
# 14.6): the web interface's certificate and key, as /etc/pve/local/pveproxy-ssl.key, and the database the API writes.
API_GROUP=www-data
if getent group "$API_GROUP" > /dev/null; then
    [ -d "$E/local" ] && chgrp "$API_GROUP" "$E/local" && chmod 750 "$E/local"
    [ -f "$E/local/hyperlite.key" ] && chgrp "$API_GROUP" "$E/local/hyperlite.key" && chmod 640 "$E/local/hyperlite.key"
    for f in "$N/hyperlite.db" "$N/hyperlite.db-wal" "$N/hyperlite.db-shm"; do
        [ -f "$f" ] && chgrp "$API_GROUP" "$f" && chmod 660 "$f"
    done
fi
[ "${#MOVED[@]}" -eq 0 ] && log "nothing to move ($1)"
exit 0
