#!/bin/bash
# Puts the Python environment the package ships in place (docs/design/clean-package.md), run by the package's postinst.
# Nothing is built or downloaded: the package carries the environment CI built from requirements.txt's hashed wheels
# (installer/build-runtime.sh), and the wheels themselves. The new environment is unpacked beside the current one,
# checked, then put in its place, which also drops what older versions had installed. When it does not work on this
# system (Debian moved Python), it is rebuilt from the wheels, without network, at its final path. When that fails too,
# the previous environment is put back and the script fails.
#
#   install-venv.sh APP_DIR RUNTIME     e.g. /root/hyperlite /usr/lib/hyperlite
set -euo pipefail
APP_DIR=${1:?usage: install-venv.sh APP_DIR RUNTIME}
RUNTIME=${2:?usage: install-venv.sh APP_DIR RUNTIME}
PREFLIGHT="$APP_DIR/app/core/preflight.py"
log() { echo "[install-venv] $*"; }

python_ok() {
    python3 "$PREFLIGHT" --only python --python "$1/bin/python3" --requirements "$APP_DIR/requirements.txt"
}

rm -rf "$APP_DIR/venv.new" "$APP_DIR/venv.old"
mkdir "$APP_DIR/venv.new"
tar -C "$APP_DIR/venv.new" --strip-components=1 -xJf "$RUNTIME/venv.tar.xz"
if python_ok "$APP_DIR/venv.new"; then
    if [ -d "$APP_DIR/venv" ]; then mv "$APP_DIR/venv" "$APP_DIR/venv.old"; fi
    mv "$APP_DIR/venv.new" "$APP_DIR/venv"
    rm -rf "$APP_DIR/venv.old"
    log "the shipped environment is in place"
    exit 0
fi

log "the shipped environment does not work here: rebuilt from the package's wheels, without network"
rm -rf "$APP_DIR/venv.new"
if [ -d "$APP_DIR/venv" ]; then mv "$APP_DIR/venv" "$APP_DIR/venv.old"; fi
reqs=$(mktemp)
trap 'rm -f "$reqs"' EXIT
# By version: libvirt-python's wheel was built in CI from its source package, whose hash was checked there.
sed -E -e 's/[[:space:]]*\\$//' -e '/^[[:space:]]*--hash=/d' "$RUNTIME/wheels/requirements.txt" > "$reqs"
if python3 -m venv "$APP_DIR/venv" &&
    "$APP_DIR/venv/bin/python3" -m pip install --no-input --quiet --no-index --find-links "$RUNTIME/wheels" -r "$reqs" &&
    python_ok "$APP_DIR/venv"; then
    rm -rf "$APP_DIR/venv.old"
    log "rebuilt from the wheels"
    exit 0
fi
rm -rf "$APP_DIR/venv"
if [ -d "$APP_DIR/venv.old" ]; then mv "$APP_DIR/venv.old" "$APP_DIR/venv"; fi
log "ERROR: no working Python environment; the previous one is back"
exit 1
