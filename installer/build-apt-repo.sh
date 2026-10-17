#!/bin/bash
# Builds/refreshes the signed APT repository (classic Debian layout: dists/ +
# pool/, like download.proxmox.com) from the .deb files produced by
# build-deb.sh. Output goes to installer/apt-repo/, meant to be published as is
# (GitHub Pages gh-pages branch, or any static web server).
#
# GPG signing key: generated ONCE in a keyring ISOLATED from the Git repository
# (/root/.hyperlite-apt-gpg, never committed). The private key never leaves this
# machine; only the PUBLIC key is published (in the repository itself,
# hyperlite-archive-keyring.asc), so any appliance can verify the authenticity
# of the packages without having to trust the transport network/server (the same
# principle as the Proxmox no-subscription repository).
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APT_REPO="$REPO_DIR/installer/apt-repo"
# shellcheck disable=SC1091
[ -f "$REPO_DIR/installer/apt-source.conf" ] && . "$REPO_DIR/installer/apt-source.conf"
GNUPGHOME="${GNUPGHOME:-/root/.hyperlite-apt-gpg}"
KEY_UID="Hyperlite Apt Repository <apt@hyperlite.local>"
# Passphrase of the signing key, when it has one (a CI secret): read from a private file so it never
# appears on a command line. Empty for a key without passphrase.
PASSFILE="$(mktemp)"
trap 'rm -f "$PASSFILE"' EXIT
printf '%s' "${HYPERLITE_GPG_PASSPHRASE:-}" > "$PASSFILE"

log() { echo "[build-apt-repo] $*"; }

mkdir -p "$GNUPGHOME"
chmod 700 "$GNUPGHOME"
export GNUPGHOME

if ! gpg --list-secret-keys "$KEY_UID" >/dev/null 2>&1; then
    log "generating the repository signing key (first time)"
    gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$PASSFILE" --quick-generate-key "$KEY_UID" ed25519 sign 0
fi
KEY_ID=$(gpg --list-secret-keys --with-colons "$KEY_UID" | awk -F: '/^fpr:/ {print $10; exit}')
log "signing key: $KEY_ID"

# Two suites (docs/design/updates-1.0.md, section 7): `stable`, the releases from master, and `test`, every merge
# into test. Each has its own pool directory, so a test build never reaches a stable node; the test suite lists the
# stable releases too, so a node on it moves to a final release (1.1.0~test.* sorts below 1.1.0) by itself.
SUITE="${HYPERLITE_SUITE:-stable}"
case "$SUITE" in
    stable) POOL="pool/main" ;;
    test) POOL="pool/test" ;;
    *) echo "[build-apt-repo] unknown suite $SUITE (stable or test)" >&2; exit 1 ;;
esac
mkdir -p "$APT_REPO/$POOL/h/hyperlite"

log "adding the produced .deb files to $POOL (keeps history so a downgrade stays possible)"
cp -n "$REPO_DIR"/installer/hyperlite_*_amd64.deb "$APT_REPO/$POOL/h/hyperlite/" 2>/dev/null || true

# Both suites are written again at each publication: their index only depends on the pools.
for suite in stable test; do
    dir="$APT_REPO/dists/$suite"
    mkdir -p "$dir/main/binary-amd64" "$APT_REPO/pool/main" "$APT_REPO/pool/test"
    log "generating Packages ($suite)"
    if [ "$suite" = stable ]; then pools=(pool/main); else pools=(pool/main pool/test); fi
    ( cd "$APT_REPO" && for p in "${pools[@]}"; do dpkg-scanpackages --multiversion "$p" /dev/null; done ) \
        > "$dir/main/binary-amd64/Packages"
    gzip -9fk "$dir/main/binary-amd64/Packages"

    log "generating Release ($suite)"
    ( cd "$dir" && apt-ftparchive \
        -o APT::FTPArchive::Release::Origin=Hyperlite \
        -o APT::FTPArchive::Release::Label=Hyperlite \
        -o APT::FTPArchive::Release::Suite="$suite" \
        -o APT::FTPArchive::Release::Codename="$suite" \
        -o APT::FTPArchive::Release::Architectures=amd64 \
        -o APT::FTPArchive::Release::Components=main \
        -o APT::FTPArchive::Release::Description="Hyperlite package repository ($suite)" \
        release . > Release )

    log "signing $suite (detached Release.gpg + InRelease, like a real Debian repository)"
    gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASSFILE" --default-key "$KEY_ID" \
        -abs -o "$dir/Release.gpg" "$dir/Release"
    gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASSFILE" --default-key "$KEY_ID" \
        --clearsign -o "$dir/InRelease" "$dir/Release"
done

log "exporting the public key (to install on each appliance)"
gpg --batch --yes --armor --export "$KEY_ID" > "$APT_REPO/hyperlite-archive-keyring.asc"

cat > "$APT_REPO/README.md" <<EOF
# Hyperlite APT repository

Replace \`<repository-url>\` with the address where this directory is served
(the public mirror is \`${HYPERLITE_APT_URL:-<not configured>}\`).

\`\`\`bash
curl -fsSL <repository-url>/hyperlite-archive-keyring.asc | gpg --dearmor -o /usr/share/keyrings/hyperlite-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hyperlite-archive-keyring.gpg] <repository-url> stable main" > /etc/apt/sources.list.d/hyperlite.list
apt update && apt install hyperlite
\`\`\`

Replace \`stable\` with \`test\` for the test channel: every merge into the test branch, before its release.

Note on GitHub Pages: it is a multi-node CDN without strong consistency between
files published in the same commit, so InRelease and Packages can be served out
of sync for a while (\`apt update\` then fails with "File has unexpected size").
Serving this directory from a single origin (for example nginx, see
installer/hyperlite-apt-repo.nginx.conf) avoids the problem.
EOF

log "repository ready in $APT_REPO"
