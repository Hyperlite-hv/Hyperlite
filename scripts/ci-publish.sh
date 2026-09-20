#!/bin/bash
# Publishes a release from a clean checkout: the .deb package, the signed APT repository (pushed to the
# mirror), the installation ISO (GitHub release) and the final verification.
#
# It is the single publication procedure. The "Publish" GitHub workflow runs it on the protected
# branch; it can also be run by hand wherever the signing key is available.
#
# Environment:
#   GNUPGHOME, HYPERLITE_GPG_PASSPHRASE   signing key (imported beforehand) and its passphrase, if any
#   HYPERLITE_MIRROR_REPO                 git URL of the mirror repository (default: installer/apt-source.conf);
#                                         an SSH URL with GIT_SSH_COMMAND lets a deploy key push
#   GH_TOKEN                              needed to upload the ISO release (not in dry run)
#   HYPERLITE_VERSION                     version to publish (default: current UTC time, YYYY.MM.DD.HHMM)
#   SOURCE_COMMIT                         commit recorded in the ISO notes (default: HEAD)
#   DRY_RUN=1                             build, sign and check everything but push nothing and upload nothing
#   SKIP_ISO=1                            do not build the ISO
#   SKIP_VERIFY=1                         do not wait for the public address to serve the new version (local tests)
#
# The version is not committed back to the repository: it is only stamped into the artifacts built
# here, so publishing needs no write access to the protected branches.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

log() { echo "[ci-publish] $*"; }

MIRROR_OVERRIDE="${HYPERLITE_MIRROR_REPO:-}"
# shellcheck disable=SC1091
. installer/apt-source.conf
[ -n "$MIRROR_OVERRIDE" ] && HYPERLITE_MIRROR_REPO="$MIRROR_OVERRIDE"
export HYPERLITE_APT_URL

VERSION="${HYPERLITE_VERSION:-$(date -u +%Y.%m.%d.%H%M)}"
SOURCE_COMMIT="${SOURCE_COMMIT:-$(git rev-parse HEAD)}"
DRY_RUN="${DRY_RUN:-0}"
echo "$VERSION" > VERSION
log "version $VERSION, commit ${SOURCE_COMMIT:0:8}, mirror $HYPERLITE_MIRROR_REPO, dry run: $DRY_RUN"

# 1. Start from the current mirror content: the pool keeps every published version (a downgrade stays
#    possible) and the new package is added to it.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git clone -q --depth 1 --branch gh-pages "$HYPERLITE_MIRROR_REPO" "$WORK/mirror"
mkdir -p installer/apt-repo
rsync -a --exclude=.git "$WORK/mirror/" installer/apt-repo/

# 2. Package, then the signed repository.
bash installer/build-deb.sh
bash installer/build-apt-repo.sh

# 3. Publish the repository to the mirror (verified by the script itself).
if [ "$DRY_RUN" = "1" ]; then
    log "dry run: the mirror is not updated"
else
    bash scripts/publish-gh-pages.sh installer/apt-repo "$HYPERLITE_MIRROR_REPO" "$VERSION" "${SOURCE_COMMIT:0:8}"
fi

# 4. ISO: build, checksum and signature, then the single moving release.
if [ "${SKIP_ISO:-0}" != "1" ]; then
    ISO=installer/hyperlite-appliance-amd64.iso
    bash installer/build-iso.sh "$ISO"
    bash scripts/sign-iso.sh "$ISO"
    NOTES="$(sed -e "s|@VERSION@|$VERSION|" -e "s|@COMMIT@|$SOURCE_COMMIT|" \
        -e "s|@SHA256@|$(cut -d' ' -f1 "$ISO.sha256")|" installer/iso-release-notes.tmpl)"
    if [ "$DRY_RUN" = "1" ]; then
        log "dry run: the ISO release is not updated"
    else
        TAG="appliance-iso-latest"
        ASSETS=("$ISO" "$ISO.sha256")
        [ -f "$ISO.sig" ] && ASSETS+=("$ISO.sig")
        if gh release view "$TAG" >/dev/null 2>&1; then
            gh release upload "$TAG" "${ASSETS[@]}" --clobber
            gh release edit "$TAG" --notes "$NOTES"
        else
            gh release create "$TAG" "${ASSETS[@]}" --title "Appliance ISO (latest)" --notes "$NOTES" --target master
        fi
        log "ISO published (release $TAG)"
    fi
fi

# 5. What clients see: GitHub Pages needs a few minutes to serve the new files.
if [ "$DRY_RUN" != "1" ] && [ "${SKIP_VERIFY:-0}" != "1" ]; then
    bash scripts/verify-apt-mirror.sh --expect-version "$VERSION" --wait 900
fi
log "done: version $VERSION"
