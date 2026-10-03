#!/bin/bash
# Builds hyperlite_<version>_amd64.deb from the current working tree. It
# replaces the git fetch/reset mechanism for any machine that adopts this
# package. The package is self-contained: its content is exactly the files
# tracked by Git (never any secret or data, already excluded through
# .gitignore: .env, hyperlite.db, data/ssh, data/tls...) PLUS the already
# built frontend (dashboard/dist, not tracked by Git but required at runtime).
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(cat "$REPO_DIR/VERSION")
BUILD_DIR="$REPO_DIR/installer/deb-build"
STAGE="$BUILD_DIR/hyperlite_${VERSION}"
OUT_DEB="$REPO_DIR/installer/hyperlite_${VERSION}_amd64.deb"

log() { echo "[build-deb] $*"; }

log "version $VERSION"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/root/hyperlite"

log "building the frontend (npm run build)"
( cd "$REPO_DIR/dashboard" && npm install --silent && npm run build --silent )

log "building the workstation client (installer/build-cli.sh)"
bash "$REPO_DIR/installer/build-cli.sh"

log "copying the files tracked by Git"
( cd "$REPO_DIR" && git ls-files -z ) | while IFS= read -r -d '' f; do
    # Only what a node runs is shipped. Documentation, design notes, the lab, the frontend sources (dashboard/dist
    # is added below) and the publishing tools stay in the Git repository. cfs/tests/*.c stay: cfs/meson.build
    # declares those tests, so the build of hyperlite-cfs on the node needs them.
    case "$f" in
        tests/*|.github/*|requirements-dev.txt|CLAUDE.md|AGENTS.md) continue ;;
        docs/*|lab/*|dashboard/*|cli/*) continue ;;
        README.md|CHANGELOG.md|CONTRIBUTING.md|CODE_OF_CONDUCT.md|SECURITY.md|*/README.md) continue ;;
        cfs/fuzz/*|cfs/tests/python/*|cfs/tests/cluster/*) continue ;;
        installer/build-*.sh|installer/test-package.sh|installer/iso-release-notes.tmpl) continue ;;
        installer/hyperlite-apt-repo.nginx.conf) continue ;;
        scripts/ci-publish.sh|scripts/publish-gh-pages.sh|scripts/release.sh|scripts/sign-iso.sh) continue ;;
        scripts/verify-apt-mirror.sh|scripts/systemd/*) continue ;;
    esac
    mkdir -p "$STAGE/root/hyperlite/$(dirname "$f")"
    cp "$REPO_DIR/$f" "$STAGE/root/hyperlite/$f"
done

log "adding the built frontend (dashboard/dist, not tracked by Git)"
cp -r "$REPO_DIR/dashboard/dist" "$STAGE/root/hyperlite/dashboard/dist"

log "adding the workstation client (cli/dist, not tracked by Git)"
cp -r "$REPO_DIR/cli/dist" "$STAGE/root/hyperlite/cli/dist"

# VERSION is copied explicitly (not only through `git ls-files`): the file may
# not be tracked yet, in which case it would be missing from the package
# despite existing on disk. Copying it here keeps the embedded content always
# the one used to NAME the package.
cp "$REPO_DIR/VERSION" "$STAGE/root/hyperlite/VERSION"

log "control/postinst"
sed "s/__VERSION__/$VERSION/" "$REPO_DIR/installer/deb/control.template" > "$STAGE/DEBIAN/control"
cp "$REPO_DIR/installer/deb/postinst" "$STAGE/DEBIAN/postinst"
chmod 755 "$STAGE/DEBIAN/postinst"

log "dpkg-deb --build"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT_DEB"

log "package built: $OUT_DEB"
dpkg-deb --info "$OUT_DEB" | grep -E "Package|Version|Depends"
