#!/bin/bash
# Builds the update file of a release, for nodes without network (app/core/offline_update.py,
# docs/design/updates-1.0.md step 6): an uncompressed tar of the hyperlite package, the ISO's offline bundle
# (installer/build-offline-bundle.sh: the Debian packages and the Python wheels), the release notes, and a manifest
# of every file with its SHA-256, signed by the key of the APT repository, which every node already trusts.
#
# Usage: build-update-file.sh VERSION DEB BUNDLE_DIR NOTES_DIR OUT
#   GNUPGHOME and HYPERLITE_GPG_PASSPHRASE: the signing key, as for installer/build-apt-repo.sh.
set -euo pipefail

VERSION="$1"
DEB="$2"
BUNDLE="$3"
NOTES="$4"
OUT="$5"

log() { echo "[update-file] $*"; }

[ -f "$DEB" ] || { echo "[update-file] no package $DEB" >&2; exit 1; }
[ -s "$BUNDLE/debs/packages.index" ] || { echo "[update-file] no offline bundle in $BUNDLE" >&2; exit 1; }
DEBIAN_MAJOR=$(sed -n 's/^debian=\([0-9]*\).*/\1/p' "$BUNDLE/bundle-info")
[ -n "$DEBIAN_MAJOR" ] || { echo "[update-file] $BUNDLE/bundle-info does not name its Debian release" >&2; exit 1; }

STAGE="$(mktemp -d)"
PASSFILE="$(mktemp)"
trap 'rm -rf "$STAGE" "$PASSFILE" "$PASSFILE.manifest"' EXIT
printf '%s' "${HYPERLITE_GPG_PASSPHRASE:-}" > "$PASSFILE"

cp "$DEB" "$STAGE/hyperlite_${VERSION}_amd64.deb"
mkdir -p "$STAGE/offline"
cp -r "$BUNDLE/debs" "$BUNDLE/wheels" "$BUNDLE/bundle-info" "$STAGE/offline/"
for lang in en fr; do
    if [ -f "$NOTES/$VERSION.$lang.md" ]; then
        mkdir -p "$STAGE/notes"
        cp "$NOTES/$VERSION.$lang.md" "$STAGE/notes/"
    fi
done

log "manifest of $(find "$STAGE" -type f | wc -l) files"
( cd "$STAGE" && find . -type f -printf '%P\n' | sort | python3 -c '
import hashlib, json, sys
version, debian = sys.argv[1], sys.argv[2]
files = {}
for name in sys.stdin.read().split():
    h = hashlib.sha256()
    with open(name, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    files[name] = h.hexdigest()
json.dump({"version": version, "debian": debian, "files": files}, sys.stdout, indent=1, sort_keys=True)
' "$VERSION" "$DEBIAN_MAJOR" > "$PASSFILE.manifest" )  # not in STAGE: find would list it
mv "$PASSFILE.manifest" "$STAGE/manifest.json"

log "signing the manifest"
KEY_ID=$(gpg --list-secret-keys --with-colons | awk -F: '/^fpr:/ {print $10; exit}')
gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASSFILE" --default-key "$KEY_ID" \
    --detach-sign -o "$STAGE/manifest.json.sig" "$STAGE/manifest.json"

# Uncompressed: the packages and wheels already are, and the node reads the manifest without unpacking anything.
# The manifest first, so a node checks the signature without reading through the whole archive.
( cd "$STAGE" && tar --owner=0 --group=0 --numeric-owner -cf "$OUT" manifest.json manifest.json.sig \
    $(find . -mindepth 1 -maxdepth 1 ! -name 'manifest.json*' -printf '%P\n' | sort) )
sha256sum "$OUT" | sed "s#  .*#  $(basename "$OUT")#" > "$OUT.sha256"
log "done: $OUT ($(du -h "$OUT" | cut -f1))"
