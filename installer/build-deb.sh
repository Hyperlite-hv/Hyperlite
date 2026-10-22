#!/bin/bash
# Builds hyperlite_<version>_amd64.deb from the current working tree. It
# replaces the git fetch/reset mechanism for any machine that adopts this
# package. The package is self-contained: its content is exactly the files
# tracked by Git (never any secret or data, already excluded through
# .gitignore: .env, hyperlite.db, data/ssh, data/tls...) PLUS the already
# built frontend (dashboard/dist, not tracked by Git but required at runtime).
#
# From 1.1 the files go where Proxmox VE keeps its own (app/core/paths.py): the code in /usr/lib/hyperlite, the web
# interface in /usr/share/hyperlite/www. The package never ships anything under /etc/hyperlite or /var/lib/hyperlite:
# that is the node's own (postinst, scripts/migrate_layout.sh).
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(cat "$REPO_DIR/VERSION")
# From 1.0.0 the versions are semver, and the package carries the epoch 1: (docs/design/updates-1.0.md): without it,
# apt orders 1.0.0 below the dated versions published before. The file names and /health keep the bare version.
if [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(~[a-z0-9.]+)?$ ]]; then
    DEB_VERSION="1:$VERSION"
else
    DEB_VERSION="$VERSION"
fi
BUILD_DIR="$REPO_DIR/installer/deb-build"
STAGE="$BUILD_DIR/hyperlite_${VERSION}"
OUT_DEB="$REPO_DIR/installer/hyperlite_${VERSION}_amd64.deb"
CODE="$STAGE/usr/lib/hyperlite"
WWW="$STAGE/usr/share/hyperlite/www"

log() { echo "[build-deb] $*"; }

log "version $VERSION"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$CODE"

log "building the frontend (npm run build)"
( cd "$REPO_DIR/dashboard" && npm install --silent && npm run build --silent )

log "building the workstation client (installer/build-cli.sh)"
bash "$REPO_DIR/installer/build-cli.sh"

log "copying the files tracked by Git"
( cd "$REPO_DIR" && git ls-files -z ) | while IFS= read -r -d '' f; do
    # Only what a node runs is shipped. Documentation, design notes, the lab, the tests, the frontend and client
    # sources (their dist/ is added below), the ISO installer, the package's own sources (dpkg keeps DEBIAN/) and
    # the publishing tools stay in the Git repository. The node builds hyperlite-cfs without its C tests.
    case "$f" in
        tests/*|.github/*|requirements-dev.txt|CLAUDE.md|AGENTS.md|.gitignore|pyproject.toml) continue ;;
        cfs/tests/*|installer/deb/*|installer/preseed.cfg|installer/partman-auto.sh|installer/postinstall.sh) continue ;;
        installer/packages.list|installer/preseed-*.cfg|installer/hyperlite-questions.sh|installer/hyperlite.templates) continue ;;
        docs/*|lab/*|dashboard/*|cli/*) continue ;;
        README.md|CHANGELOG.md|CONTRIBUTING.md|CODE_OF_CONDUCT.md|SECURITY.md|*/README.md|changelog.d/*) continue ;;
        cfs/*|scripts/build-cfs.sh) continue ;;  # built in CI (installer/build-runtime.sh): no sources to build on a node
        installer/build-*.sh|installer/test-package.sh|installer/iso-release-notes.tmpl) continue ;;
        installer/hyperlite-apt-repo.nginx.conf) continue ;;
        scripts/ci-publish.sh|scripts/publish-gh-pages.sh|scripts/release.sh|scripts/release-notes.sh|scripts/sign-iso.sh) continue ;;
        scripts/changelog.py) continue ;;
        scripts/verify-apt-mirror.sh) continue ;;
    esac
    mkdir -p "$CODE/$(dirname "$f")"
    cp "$REPO_DIR/$f" "$CODE/$f"
done

log "adding the built frontend (dashboard/dist, not tracked by Git) as /usr/share/hyperlite/www"
mkdir -p "$(dirname "$WWW")"
cp -r "$REPO_DIR/dashboard/dist" "$WWW"

log "adding the workstation client (cli/dist, not tracked by Git)"
mkdir -p "$CODE/cli"  # its sources are not shipped, so the copy loop did not create it
cp -r "$REPO_DIR/cli/dist" "$CODE/cli/dist"

# What a node used to build for itself (docs/design/clean-package.md): hyperlite-cfs, the ready Python environment
# and the wheels it is made of, from installer/build-runtime.sh (CI: the debian:13 container).
RUNTIME_DIR="${HYPERLITE_RUNTIME:-$REPO_DIR/installer/runtime}"
for f in hyperlite-cfs venv.tar.xz wheels/requirements.txt; do
    [ -e "$RUNTIME_DIR/$f" ] || { echo "[build-deb] ERROR: $RUNTIME_DIR/$f missing: run installer/build-runtime.sh first" >&2; exit 1; }
done
cmp -s "$RUNTIME_DIR/wheels/requirements.txt" "$REPO_DIR/requirements.txt" ||
    { echo "[build-deb] ERROR: $RUNTIME_DIR was built from another requirements.txt: build it again" >&2; exit 1; }
log "adding the runtime built in CI ($RUNTIME_DIR)"
install -m 755 "$RUNTIME_DIR/hyperlite-cfs" "$CODE/hyperlite-cfs"
install -m 644 "$RUNTIME_DIR/venv.tar.xz" "$CODE/venv.tar.xz"
cp -r "$RUNTIME_DIR/wheels" "$CODE/wheels"

# VERSION is copied explicitly (not only through `git ls-files`): the file may
# not be tracked yet, in which case it would be missing from the package
# despite existing on disk. Copying it here keeps the embedded content always
# the one used to NAME the package.
cp "$REPO_DIR/VERSION" "$CODE/VERSION"

log "control and maintainer scripts"
sed "s/__VERSION__/$DEB_VERSION/" "$REPO_DIR/installer/deb/control.template" > "$STAGE/DEBIAN/control"
for script in preinst postinst prerm postrm; do
    install -m 755 "$REPO_DIR/installer/deb/$script" "$STAGE/DEBIAN/$script"
done

log "dpkg-deb --build"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT_DEB"

log "package built: $OUT_DEB"
dpkg-deb --info "$OUT_DEB" | grep -E "Package|Version|Depends"
