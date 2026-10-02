#!/bin/bash
# Checks the package's system side on a distribution it is installed on, in a container of that distribution:
#   - every dependency of installer/deb/control.template resolves there (a name missing on one distribution would make
#     every upgrade fail on it);
#   - scripts/build-cfs.sh, as the postinst runs it, builds hyperlite-cfs against that distribution's libraries, and the
#     daemon starts and serves its socket;
#   - the hyperlite-cfs unit is valid for that distribution's systemd.
#
#   installer/test-package.sh IMAGE     e.g. debian:12, debian:13, ubuntu:24.04 (needs docker)
set -euo pipefail

IMAGE=${1:?usage: $0 IMAGE}
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker run --rm -i -v "$REPO_DIR:/src:ro" "$IMAGE" bash -euo pipefail -s <<'EOF'
export DEBIAN_FRONTEND=noninteractive
log() { echo "[test-package] $*"; }
. /etc/os-release
log "$PRETTY_NAME"
apt-get update -qq

# The dependencies, alternatives and version constraints removed: apt resolves each name, virtual ones included.
deps=$(sed -n 's/^Depends: //p' /src/installer/deb/control.template | tr ',' '\n' | sed 's/([^)]*)//; s/|.*//; s/ //g')
apt-get install -s -qq $deps > /dev/null
log "every dependency resolves"

# What scripts/build-cfs.sh needs, from the same list (gcc, pkg-config, meson, ninja and the -dev libraries).
build_deps=$(echo "$deps" | grep -E '^(gcc|pkg-config|meson|ninja-build|lib.*-dev)$' | grep -v libvirt-dev)
apt-get install -y -qq --no-install-recommends $build_deps systemd > /dev/null
mkdir -p /root/hyperlite/data
cp -r /src/cfs /src/scripts /root/hyperlite/
bash /root/hyperlite/scripts/build-cfs.sh /root/hyperlite
/usr/local/sbin/hyperlite-cfs --help > /dev/null

# Local mode (no Corosync): the daemon creates its database and serves the socket.
mkdir -p /run/hyperlite-cfs /var/lib/hyperlite-cfs
/usr/local/sbin/hyperlite-cfs --db /var/lib/hyperlite-cfs/config.db --socket /run/hyperlite-cfs/socket &
pid=$!
for _ in $(seq 1 50); do [ -S /run/hyperlite-cfs/socket ] && break; sleep 0.1; done
[ -S /run/hyperlite-cfs/socket ] || { log "the daemon did not create its socket"; exit 1; }
kill "$pid"
wait "$pid" || true
log "hyperlite-cfs built and started"

# The unit names corosync.service, which this container does not have; the check is about the unit's own syntax.
problems=$(systemd-analyze verify /src/installer/hyperlite-cfs.service 2>&1 | grep -v 'corosync.service' || true)
[ -z "$problems" ] || { echo "$problems"; log "the unit has errors"; exit 1; }
log "unit valid"
EOF
