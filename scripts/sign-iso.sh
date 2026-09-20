#!/bin/bash
# Writes <iso>.sha256 (checksum file usable with `sha256sum -c`) and, when the
# repository signing key is available, a detached ASCII signature <iso>.sig.
# Usage: sign-iso.sh <iso>
set -e
ISO="$1"
[ -f "$ISO" ] || { echo "usage: $0 <iso>" >&2; exit 1; }
DIR="$(dirname "$ISO")"; NAME="$(basename "$ISO")"
( cd "$DIR" && sha256sum "$NAME" > "$NAME.sha256" )
GNUPGHOME="${GNUPGHOME:-/root/.hyperlite-apt-gpg}"
KEY_UID="Hyperlite Apt Repository <apt@hyperlite.local>"
PASSFILE="$(mktemp)"
trap 'rm -f "$PASSFILE"' EXIT
printf '%s' "${HYPERLITE_GPG_PASSPHRASE:-}" > "$PASSFILE"
rm -f "$ISO.sig"
if [ -d "$GNUPGHOME" ] && GNUPGHOME="$GNUPGHOME" gpg --list-secret-keys "$KEY_UID" >/dev/null 2>&1; then
    GNUPGHOME="$GNUPGHOME" gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASSFILE" \
        --local-user "$KEY_UID" --armor --detach-sign -o "$ISO.sig" "$ISO"
    echo "signed: $ISO.sig"
else
    echo "no signing key available: only the checksum was written" >&2
fi
cat "$ISO.sha256"
