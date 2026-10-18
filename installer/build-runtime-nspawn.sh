#!/bin/bash
# Runs installer/build-runtime.sh in an isolated Debian 13 root (systemd-nspawn), for a development machine without
# Docker. The root is made once with: debootstrap --variant=minbase trixie ROOT http://deb.debian.org/debian
#
#   build-runtime-nspawn.sh ROOT OUT     ROOT: the Debian 13 root; OUT: where the runtime goes (on this machine)
#
# The repository is mounted read-only; nothing of this machine's own /root/hyperlite is visible inside.
set -euo pipefail
ROOT=${1:?usage: build-runtime-nspawn.sh ROOT OUT}
OUT=${2:?usage: build-runtime-nspawn.sh ROOT OUT}
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
systemd-nspawn --quiet --register=no -D "$ROOT" --bind-ro="$REPO_DIR:/src" --bind="$OUT:/out" \
    bash /src/installer/build-runtime.sh /src /out/runtime
