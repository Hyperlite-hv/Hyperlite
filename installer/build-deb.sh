#!/bin/bash
# Construit hyperlite_<version>_amd64.deb a partir de l'arbre de travail
# courant (chantier "apt install" de mise a jour, 2026-09-17 -- remplace le
# mecanisme git fetch/reset pour toute machine qui adopte ce paquet ;
# kvm-lab lui-meme reste en clone git pour le developpement, voir
# CLAUDE.md). Paquet auto-suffisant : contenu = exactement les fichiers
# suivis par Git (jamais de secret/donnee, deja exclus via .gitignore --
# .env, hyperlite.db, data/ssh, data/tls...) PLUS le frontend deja construit
# (dashboard/dist, non suivi par Git mais indispensable a l'execution).
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

log "construction du frontend (npm run build)"
( cd "$REPO_DIR/dashboard" && npm install --silent && npm run build --silent )

log "copie des fichiers suivis par Git"
( cd "$REPO_DIR" && git ls-files -z ) | while IFS= read -r -d '' f; do
    mkdir -p "$STAGE/root/hyperlite/$(dirname "$f")"
    cp "$REPO_DIR/$f" "$STAGE/root/hyperlite/$f"
done

log "ajout du frontend construit (dashboard/dist, non suivi par Git)"
cp -r "$REPO_DIR/dashboard/dist" "$STAGE/root/hyperlite/dashboard/dist"

# VERSION copie explicitement (pas juste via `git ls-files`) : BUG REEL
# trouve en testant -- ce fichier n'avait jamais ete `git add`e au moment du
# tout premier paquet construit, donc absent du paquet malgre sa presence
# sur disque ("termine (version ?)" dans les logs postinst). En copiant ici
# explicitement, le contenu embarque reste toujours celui utilise pour NOMMER
# le paquet (coherence garantie), qu'il soit deja suivi par Git ou non.
cp "$REPO_DIR/VERSION" "$STAGE/root/hyperlite/VERSION"

log "control/postinst"
sed "s/__VERSION__/$VERSION/" "$REPO_DIR/installer/deb/control.template" > "$STAGE/DEBIAN/control"
cp "$REPO_DIR/installer/deb/postinst" "$STAGE/DEBIAN/postinst"
chmod 755 "$STAGE/DEBIAN/postinst"

log "dpkg-deb --build"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT_DEB"

log "paquet produit : $OUT_DEB"
dpkg-deb --info "$OUT_DEB" | grep -E "Package|Version|Depends"
