#!/bin/bash
# Checks what the package ships on Debian 13, in a container (installer/test-package-inside.sh says what).
#
#   installer/test-package.sh IMAGE RUNTIME     e.g. debian:13 installer/runtime (needs docker; RUNTIME from
#                                                installer/build-runtime.sh)
set -euo pipefail
IMAGE=${1:?usage: $0 IMAGE RUNTIME}
RUNTIME=${2:?usage: $0 IMAGE RUNTIME}
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$(cd "$RUNTIME" && pwd)"
# HYPERLITE_BUILD_CACHE: a directory of this machine where apt keeps its downloads between runs (installer/build-cache.sh).
cache=()
if [ -n "${HYPERLITE_BUILD_CACHE:-}" ]; then
    cache=(-v "$HYPERLITE_BUILD_CACHE:/cache" -e HYPERLITE_BUILD_CACHE=/cache)
fi
docker run --rm -v "$REPO_DIR:/src:ro" -v "$RUNTIME:/runtime:ro" "${cache[@]}" "$IMAGE" bash /src/installer/test-package-inside.sh
