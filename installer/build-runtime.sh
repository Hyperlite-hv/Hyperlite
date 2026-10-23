#!/bin/bash
# Builds what a node used to build for itself at every installation (docs/design/clean-package.md): hyperlite-cfs, the
# Python wheels of requirements.txt and the ready Python environment made of them. installer/build-deb.sh puts them in
# the package; postinst no longer runs a compiler or pip against the Internet.
#
#   build-runtime.sh SRC OUT     SRC: the repository; OUT: where hyperlite-cfs, wheels/ and venv.tar.xz go
#
# Runs as root in a Debian 13 and NEVER on a node: the environment is built at its final path, /usr/lib/hyperlite/venv,
# so its scripts start the right interpreter. CI runs it in the debian:13 container; installer/build-runtime-nspawn.sh
# runs it in an isolated Debian 13 root on a development machine.
set -euo pipefail

SRC=${1:?usage: build-runtime.sh SRC OUT}
OUT=${2:?usage: build-runtime.sh SRC OUT}
log() { echo "[build-runtime] $*"; }
fail() { echo "[build-runtime] ERROR: $*" >&2; exit 1; }

. /etc/os-release
[ "${VERSION_ID:-}" = 13 ] || fail "Debian 13 expected, this is ${PRETTY_NAME:-unknown}: the package runs on Debian 13 only"
for d in /usr/lib/hyperlite /root/hyperlite; do
    [ ! -e "$d/app" ] || fail "$d holds an installation: this is a node, not a build environment"
done

log "build dependencies"
export DEBIAN_FRONTEND=noninteractive
. "$SRC/installer/build-cache.sh"
apt-get update -qq
apt_trust_kept_debs
apt-get install -y -qq --no-install-recommends ca-certificates gcc pkg-config meson ninja-build xz-utils \
    libsqlite3-dev libssl-dev libcpg-dev libquorum-dev libvotequorum-dev libcmap-dev libvirt-dev \
    python3 python3-venv python3-dev > /dev/null

rm -rf "$OUT" && mkdir -p "$OUT/wheels"
work=$(mktemp -d)

log "hyperlite-cfs"
# The same build postinst used to run on the node (scripts/build-cfs.sh), the C tests left out.
meson setup --buildtype=release -Dwerror=false -Dtests=false "$work/cfs" "$SRC/cfs" > "$work/cfs.log" 2>&1 ||
    { cat "$work/cfs.log" >&2; fail "meson setup"; }
ninja -C "$work/cfs" hyperlite-cfs >> "$work/cfs.log" 2>&1 || { tail -n 40 "$work/cfs.log" >&2; fail "ninja"; }
install -m 755 "$work/cfs/hyperlite-cfs" "$OUT/hyperlite-cfs"

log "wheels (every download checked against the hashes of requirements.txt)"
python3 -m venv "$work/pip"
"$work/pip/bin/pip" wheel -q --require-hashes -r "$SRC/requirements.txt" -w "$OUT/wheels"
cp "$SRC/requirements.txt" "$OUT/wheels/requirements.txt"
if find "$OUT/wheels" -type f ! -name '*.whl' ! -name requirements.txt | grep -q .; then
    fail "a requirement did not become a wheel"
fi

log "the Python environment, at its final path"
python3 -m venv /usr/lib/hyperlite/venv
# By version: libvirt-python's wheel was just built from its source package, whose hash was checked above.
sed -E -e 's/[[:space:]]*\\$//' -e '/^[[:space:]]*--hash=/d' "$SRC/requirements.txt" > "$work/requirements"
/usr/lib/hyperlite/venv/bin/pip install -q --no-index --find-links "$OUT/wheels" -r "$work/requirements"
/usr/lib/hyperlite/venv/bin/pip check
tar -C /usr/lib/hyperlite --sort=name --owner=0 --group=0 --numeric-owner -cJf "$OUT/venv.tar.xz" venv
rm -rf /usr/lib/hyperlite/venv "$work"
rmdir /usr/lib/hyperlite 2>/dev/null || true

if [ -n "${HOST_UID:-}" ]; then
    chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$OUT"
fi
log "done: $(du -sh "$OUT" | cut -f1) in $OUT"
