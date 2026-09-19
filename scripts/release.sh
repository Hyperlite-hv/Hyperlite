#!/bin/bash
# Publishes an immutable, versioned release: a git tag on the current commit and a
# GitHub release carrying the appliance ISO, its SHA-256 checksum and signature.
# Run on the build machine, from an up-to-date, clean master checkout.
#
# Usage: scripts/release.sh vX.Y.Z [--dry-run]
set -e
TAG="$1"; DRY="$2"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: $0 vX.Y.Z [--dry-run]" >&2; exit 1; }
cd "$(git rev-parse --show-toplevel)"
[ "$(git rev-parse --abbrev-ref HEAD)" = "master" ] || { echo "run from master" >&2; exit 1; }
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] || { echo "master is not up to date with origin" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree is not clean" >&2; exit 1; }
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && { echo "tag $TAG already exists" >&2; exit 1; }

NOTES=$(mktemp)
# Release notes: the [Unreleased] section of CHANGELOG.md plus the traceability data.
awk '/^## \[Unreleased\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md > "$NOTES"
{
    echo
    echo "Source commit: $(git rev-parse HEAD)"
    echo "Package version: $(cat VERSION)"
    echo "Verify the download with: sha256sum -c hyperlite-appliance-amd64-$TAG.iso.sha256"
} >> "$NOTES"

ISO="installer/hyperlite-appliance-amd64-$TAG.iso"
bash installer/build-deb.sh
bash installer/build-iso.sh "$ISO"
bash scripts/sign-iso.sh "$ISO"
ASSETS=("$ISO" "$ISO.sha256")
[ -f "$ISO.sig" ] && ASSETS+=("$ISO.sig")

if [ "$DRY" = "--dry-run" ]; then
    echo "dry run: would tag $TAG and publish: ${ASSETS[*]}"; cat "$NOTES"; exit 0
fi
git tag -a "$TAG" -m "Hyperlite $TAG"
git push origin "$TAG"
gh release create "$TAG" "${ASSETS[@]}" --title "Hyperlite $TAG" --notes-file "$NOTES" --verify-tag
