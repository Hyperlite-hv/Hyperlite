#!/bin/bash
# Builds what the ISO carries so that an installation needs no network, as with Proxmox:
#   OUT/debs/      every Debian package the appliance installs, with all their dependencies, and an APT index;
#   OUT/wheels/    the Python packages of requirements.txt, as wheels built for the target's Python
#                  (libvirt-python included, so nothing is compiled from PyPI at installation);
#   OUT/install.list  the packages postinstall.sh installs: installer/packages.list plus the "standard" and
#                  "ssh-server" tasks the Debian installer used to select;
#   OUT/hardware.list the packages postinstall.sh installs depending on the machine (the processor's microcode,
#                  the guest agent in a VM), as the Debian installer does when it has a network.
#
# Run it as root in a Debian of the same release as the ISO (installer/build-iso.sh, DEBIAN_VERSION): the packages
# and the wheels must match what the installer puts on the disk. scripts/ci-publish.sh runs it in a debian container.
#
# Usage: build-offline-bundle.sh OUT_DIR
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT="${1:?usage: build-offline-bundle.sh OUT_DIR}"
# Absolute: apt reads a relative Dir::Cache::archives from its own cache directory, not from here.
OUT="$(realpath -m "$OUT")"

log() { echo "[offline-bundle] $*"; }
fail() { echo "[offline-bundle] ERROR: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || fail "run as root (apt-get)"
# shellcheck disable=SC1091
. /etc/os-release
WANTED=$(sed -n 's/^DEBIAN_VERSION="\([0-9]*\)\..*/\1/p' "$SCRIPT_DIR/build-iso.sh")
[ "${VERSION_ID:-}" = "$WANTED" ] || fail "this is Debian ${VERSION_ID:-?}, the ISO installs Debian $WANTED"

export DEBIAN_FRONTEND=noninteractive
rm -rf "$OUT"
mkdir -p "$OUT/debs/partial" "$OUT/wheels"

log "tools"
# The processors' microcode is in non-free-firmware, which a debian container does not enable.
if [ -f /etc/apt/sources.list.d/debian.sources ]; then
    sed -i 's/^Components: main$/Components: main contrib non-free-firmware/' /etc/apt/sources.list.d/debian.sources
fi
apt-get update -qq
apt-get install -y -qq --no-install-recommends apt-utils tasksel python3 python3-venv python3-dev python3-pip \
    pkg-config gcc libvirt-dev > /dev/null

log "the list of packages"
# The package's own dependencies (Depends and Recommends of the control file, first choice of each alternative,
# without version constraints) are downloaded too; postinstall.sh installs the package itself.
package_deps=$(sed -n 's/^\(Depends\|Recommends\): //p' "$SCRIPT_DIR/deb/control.template" | tr ',' '\n' |
    sed -e 's/|.*//' -e 's/(.*)//' -e 's/[[:space:]]//g' | awk 'NF')
{
    sed 's/#.*//' "$SCRIPT_DIR/packages.list" | awk 'NF {print $1}'
    tasksel --task-packages standard
    tasksel --task-packages ssh-server
} | sort -u > "$OUT/install.list"
printf '%s\n' intel-microcode amd64-microcode qemu-guest-agent > "$OUT/hardware.list"
# The packages of priority required and important are installed from the ISO's base system; their latest versions
# (security updates included) are carried too, so the new machine is as up to date as the bundle.
# Read through apt-cache: the indexes on disk may be compressed (Debian's container images keep them as
# *_Packages.lz4), which the CI's debian:13 container does and a VM does not.
base=$(apt-cache dumpavail | awk '/^Package:/{p=$2} /^Priority: (required|important)$/{print p}' | sort -u)
[ -n "$base" ] || { echo "no package of priority required or important found in the apt indexes" >&2; exit 1; }

log "downloading $(wc -l < "$OUT/install.list") packages and everything they need"
# An empty dpkg status: apt resolves the whole set from scratch, as for a machine that has nothing installed, so
# every dependency is downloaded even when this build system already has it. Recommends are included, as the
# Debian installer does.
empty_status=$(mktemp)
# shellcheck disable=SC2046
apt-get install -y -qq --download-only \
    -o Dir::State::status="$empty_status" \
    -o Dir::Cache::archives="$OUT/debs" \
    -o APT::Install-Recommends=true \
    -o APT::Sandbox::User=root \
    $(cat "$OUT/install.list" "$OUT/hardware.list") $package_deps $base > /dev/null
rm -rf "$empty_status" "$OUT/debs/partial" "$OUT/debs/lock"

count=$(find "$OUT/debs" -name "*.deb" | wc -l)
[ "$count" -gt 0 ] || fail "no package was downloaded"
log "APT index ($count packages)"
# Not named Packages: the Debian installer scans the CD for Packages files and would add this unsigned directory
# as a source of its own, which breaks its CD source (console-setup and the microcode then fail to install).
# postinstall.sh copies it to Packages in its copy of the bundle.
( cd "$OUT/debs" && apt-ftparchive packages . > packages.index )

log "Python wheels"
venv=$(mktemp -d)
python3 -m venv "$venv"
"$venv/bin/pip" install -q --upgrade pip wheel setuptools
"$venv/bin/pip" wheel -q -r "$ROOT/requirements.txt" -w "$OUT/wheels"
# postinst upgrades pip in the new venv first: carry it too.
"$venv/bin/pip" download -q --only-binary=:all: pip -d "$OUT/wheels"
rm -rf "$venv"
if find "$OUT/wheels" -type f ! -name '*.whl' | grep -q .; then
    fail "a requirement did not become a wheel: $(find "$OUT/wheels" -type f ! -name '*.whl' -printf '%f ')"
fi

{
    echo "debian=$VERSION_ID ($VERSION_CODENAME)"
    echo "python=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    echo "debs=$(find "$OUT/debs" -name '*.deb' | wc -l)"
    echo "wheels=$(find "$OUT/wheels" -name '*.whl' | wc -l)"
    echo "built_at=$(date -u +%FT%TZ)"
} > "$OUT/bundle-info"
# In a container, hand the files back to the user who started it.
[ -n "${HOST_UID:-}" ] && chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" "$OUT"
log "done: $(tr '\n' ' ' < "$OUT/bundle-info")($(du -sh "$OUT" | cut -f1))"
