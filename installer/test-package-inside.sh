#!/bin/bash
# What installer/test-package.sh runs inside a Debian 13: the repository at /src, the runtime of
# installer/build-runtime.sh at /runtime (both read-only). Checks what the package ships, as a node uses it:
#   - every dependency of installer/deb/control.template resolves;
#   - the shipped hyperlite-cfs starts and serves its socket, and its unit is valid;
#   - the shipped Python environment is put in place by scripts/install-venv.sh and imports Hyperlite's modules;
#   - an environment that does not work here is rebuilt from the shipped wheels without any index;
#   - when that fails too, the previous environment is back and the script fails.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
log() { echo "[test-package] $*"; }
. /etc/os-release
log "$PRETTY_NAME"
apt-get update -qq

# The dependencies, alternatives and version constraints removed: apt resolves each name, virtual ones included.
deps=$(sed -n 's/^Depends: //p' /src/installer/deb/control.template | tr ',' '\n' | sed 's/([^)]*)//; s/|.*//; s/ //g')
apt-get install -s -qq $deps > /dev/null
log "every dependency resolves"
if echo "$deps" | grep -Eq '^(gcc|pkg-config|meson|ninja-build|python3-pip|lib.*-dev)$'; then
    log "a build tool is a dependency: a node must build nothing"; exit 1
fi
# What the shipped files run with: the libraries, Python and systemd (for the unit check).
runtime_deps=$(echo "$deps" | grep -E '^(lib|python3|xz-utils)')
apt-get install -y -qq --no-install-recommends $runtime_deps systemd > /dev/null

mkdir -p /usr/lib/hyperlite
cp -r /runtime/. /usr/lib/hyperlite/
cp -r /src/app /src/scripts /src/requirements.txt /usr/lib/hyperlite/

/usr/lib/hyperlite/hyperlite-cfs --help > /dev/null
mkdir -p /run/hyperlite-cfs /var/lib/hyperlite-cfs
/usr/lib/hyperlite/hyperlite-cfs --db /var/lib/hyperlite-cfs/config.db --socket /run/hyperlite-cfs/socket &
pid=$!
for _ in $(seq 1 50); do [ -S /run/hyperlite-cfs/socket ] && break; sleep 0.1; done
[ -S /run/hyperlite-cfs/socket ] || { log "the daemon did not create its socket"; exit 1; }
kill "$pid"
wait "$pid" || true
log "the shipped hyperlite-cfs starts"
# The unit names corosync.service, which this container does not have; the check is about the unit's own syntax.
problems=$(systemd-analyze verify /src/installer/hyperlite-cfs.service 2>&1 | grep -v 'corosync.service' || true)
[ -z "$problems" ] || { echo "$problems"; log "the unit has errors"; exit 1; }
log "unit valid"

imports() { /usr/lib/hyperlite/venv/bin/python3 -c "import fastapi, uvicorn, libvirt, cryptography, asyncssh, ldap3, webauthn"; }
bash /usr/lib/hyperlite/scripts/install-venv.sh /usr/lib/hyperlite /usr/lib/hyperlite | tee /tmp/venv.log
grep -q "shipped environment is in place" /tmp/venv.log && imports
log "the shipped environment works"

# An environment whose interpreter is gone (Debian moved Python): rebuilt from the wheels, no index reachable.
broken=$(mktemp -d)
tar -C "$broken" -xJf /runtime/venv.tar.xz
for py in "$broken"/venv/bin/python*; do ln -sfn /nonexistent/python3 "$py"; done
tar -C "$broken" -cJf /usr/lib/hyperlite/venv.tar.xz venv
PIP_NO_INDEX=1 bash /usr/lib/hyperlite/scripts/install-venv.sh /usr/lib/hyperlite /usr/lib/hyperlite | tee /tmp/venv.log
grep -q "rebuilt from the wheels" /tmp/venv.log && imports
log "a broken environment is rebuilt from the wheels without network"

# Neither works: the previous environment stays.
mv /usr/lib/hyperlite/wheels /usr/lib/hyperlite/wheels.away
mkdir /usr/lib/hyperlite/wheels && cp /usr/lib/hyperlite/wheels.away/requirements.txt /usr/lib/hyperlite/wheels/
if bash /usr/lib/hyperlite/scripts/install-venv.sh /usr/lib/hyperlite /usr/lib/hyperlite; then
    log "it should have failed"; exit 1
fi
imports
log "when nothing works, the previous environment is back"
