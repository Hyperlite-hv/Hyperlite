#!/bin/bash
# Builds hyperlite-cfs (cfs/) on this machine and installs it as /usr/local/sbin/hyperlite-cfs. The package's postinst
# runs it on every installation and upgrade: the daemon links the distribution's own Corosync and SQLite libraries,
# so it is built against them here, as libvirt-python is by pip, rather than shipped prebuilt for one distribution.
#
#   build-cfs.sh [APP_DIR [PREFIX]]   APP_DIR: the installed application (default /root/hyperlite);
#                                     PREFIX: where sbin/hyperlite-cfs goes (default /usr/local)
#
# Nothing starts the daemon: its unit (hyperlite-cfs.service) stays disabled until a cluster is set up. A running
# daemon keeps the binary it started with until it is restarted, since the new one replaces the file by a rename.
set -euo pipefail

APP_DIR=${1:-/root/hyperlite}
PREFIX=${2:-/usr/local}
BUILD="$APP_DIR/data/cfs-build"

log() { echo "[build-cfs] $*"; }

# Werror stays for the developers' builds; a newer compiler on the target must not turn a new warning into a failed
# installation.
rm -rf "${BUILD:?}"
meson setup --buildtype=release -Dwerror=false "$BUILD" "$APP_DIR/cfs" > "$BUILD.log" 2>&1 ||
    { cat "$BUILD.log" >&2; exit 1; }
ninja -C "$BUILD" hyperlite-cfs >> "$BUILD.log" 2>&1 || { tail -n 40 "$BUILD.log" >&2; exit 1; }

mkdir -p "$PREFIX/sbin"
install -m 755 "$BUILD/hyperlite-cfs" "$PREFIX/sbin/.hyperlite-cfs.new"
mv -f "$PREFIX/sbin/.hyperlite-cfs.new" "$PREFIX/sbin/hyperlite-cfs"
rm -rf "${BUILD:?}" "$BUILD.log"
log "installed $PREFIX/sbin/hyperlite-cfs"
