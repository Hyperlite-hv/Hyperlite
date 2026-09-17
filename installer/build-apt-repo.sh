#!/bin/bash
# Construit/actualise le depot APT signe (structure Debian classique --
# dists/ + pool/, comme download.proxmox.com) a partir des .deb produits
# par build-deb.sh. Sortie dans installer/apt-repo/, destinee a etre
# publiee telle quelle sur GitHub Pages (branche gh-pages).
#
# Cle de signature GPG : generee UNE FOIS dans un trousseau ISOLE hors du
# depot Git (/root/.hyperlite-apt-gpg, jamais commite) -- la cle privee ne
# quitte jamais cette machine ; seule la cle PUBLIQUE est publiee (dans le
# depot lui-meme, hyperlite-archive-keyring.asc) pour que n'importe quelle
# appliance puisse verifier l'authenticite des paquets sans jamais avoir a
# faire confiance au reseau/serveur de transport (meme principe que le
# depot Proxmox no-subscription).
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APT_REPO="$REPO_DIR/installer/apt-repo"
GNUPGHOME="/root/.hyperlite-apt-gpg"
KEY_UID="Hyperlite Apt Repository <apt@hyperlite.local>"

log() { echo "[build-apt-repo] $*"; }

mkdir -p "$GNUPGHOME"
chmod 700 "$GNUPGHOME"
export GNUPGHOME

if ! gpg --list-secret-keys "$KEY_UID" >/dev/null 2>&1; then
    log "génération de la clé de signature du dépôt (première fois)"
    gpg --batch --quiet --pinentry-mode loopback --passphrase '' --quick-generate-key "$KEY_UID" ed25519 sign 0
fi
KEY_ID=$(gpg --list-secret-keys --with-colons "$KEY_UID" | awk -F: '/^fpr:/ {print $10; exit}')
log "clé de signature : $KEY_ID"

mkdir -p "$APT_REPO/pool/main/h/hyperlite" "$APT_REPO/dists/stable/main/binary-amd64"

log "ajout des .deb produits au pool (conserve l'historique pour permettre un downgrade)"
cp -n "$REPO_DIR"/installer/hyperlite_*_amd64.deb "$APT_REPO/pool/main/h/hyperlite/" 2>/dev/null || true

log "génération de Packages"
( cd "$APT_REPO" && dpkg-scanpackages --multiversion pool /dev/null > dists/stable/main/binary-amd64/Packages )
gzip -9fk "$APT_REPO/dists/stable/main/binary-amd64/Packages"

log "génération de Release"
( cd "$APT_REPO/dists/stable" && apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=Hyperlite \
    -o APT::FTPArchive::Release::Label=Hyperlite \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Codename=stable \
    -o APT::FTPArchive::Release::Architectures=amd64 \
    -o APT::FTPArchive::Release::Components=main \
    -o APT::FTPArchive::Release::Description="Dépôt de paquets Hyperlite" \
    release . > Release )

log "signature (Release.gpg détachée + InRelease, comme un dépôt Debian réel)"
gpg --batch --yes --pinentry-mode loopback --passphrase '' --default-key "$KEY_ID" \
    -abs -o "$APT_REPO/dists/stable/Release.gpg" "$APT_REPO/dists/stable/Release"
gpg --batch --yes --pinentry-mode loopback --passphrase '' --default-key "$KEY_ID" \
    --clearsign -o "$APT_REPO/dists/stable/InRelease" "$APT_REPO/dists/stable/Release"

log "export de la clé publique (à installer sur chaque appliance)"
gpg --batch --yes --armor --export "$KEY_ID" > "$APT_REPO/hyperlite-archive-keyring.asc"

cat > "$APT_REPO/README.md" <<EOF
# Dépôt APT Hyperlite

Sur une appliance/machine à faire pointer vers ce dépôt :

\`\`\`bash
curl -fsSL https://twikles.github.io/hyperlite/hyperlite-archive-keyring.asc | gpg --dearmor -o /usr/share/keyrings/hyperlite-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hyperlite-archive-keyring.gpg] https://twikles.github.io/hyperlite stable main" > /etc/apt/sources.list.d/hyperlite.list
apt update && apt install hyperlite
\`\`\`
EOF

log "dépôt prêt dans $APT_REPO"
