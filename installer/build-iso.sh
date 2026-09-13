#!/bin/bash
# Construit l'ISO bootable "Hyperlite Appliance" : part d'un netinst Debian
# stable officiel, y injecte le preseed + les scripts d'installation + une
# copie du code Hyperlite (app + dashboard deja buildee), et modifie le menu
# de boot pour que l'installation automatisee demarre par defaut apres un
# court delai -- meme logique que la cle USB Proxmox ("boote, attends, c'est
# installe").
#
# Usage : ./build-iso.sh [chemin_iso_sortie.iso]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HYPERLITE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ISO="${1:-$SCRIPT_DIR/hyperlite-appliance-amd64.iso}"
CACHE_DIR="$SCRIPT_DIR/.iso-cache"

# Debian 13 (trixie), stable courante au moment de l'ecriture de ce script --
# recoit les mises a jour de securite, contrairement a bookworm (12) deja
# archivee. A ajuster ici si une version plus recente sort.
DEBIAN_VERSION="13.6.0"
ISO_NAME="debian-${DEBIAN_VERSION}-amd64-netinst.iso"
ISO_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/${ISO_NAME}"
ISO_SHA256="65273beed27b2df543b68b65630ba525cfbad8df2b12035732b2dff87d6664e7"

log() { echo "[build-iso] $*"; }

for bin in xorriso openssl rsync; do
    command -v "$bin" >/dev/null || { echo "ERREUR : $bin requis (apt install $bin)"; exit 1; }
done
ISOHDPFX=/usr/lib/ISOLINUX/isohdpfx.bin
[ -f "$ISOHDPFX" ] || { echo "ERREUR : paquet isolinux requis (apt install isolinux)"; exit 1; }

mkdir -p "$CACHE_DIR"
BASE_ISO="$CACHE_DIR/$ISO_NAME"

log "=== 1/6 : ISO Debian de base ==="
if [ -f "$BASE_ISO" ] && echo "$ISO_SHA256  $BASE_ISO" | sha256sum -c - >/dev/null 2>&1; then
    log "deja en cache et verifiee : $BASE_ISO"
else
    log "telechargement : $ISO_URL"
    curl -fL --progress-bar -o "$BASE_ISO" "$ISO_URL"
    echo "$ISO_SHA256  $BASE_ISO" | sha256sum -c -
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT
EXTRACT_DIR="$WORKDIR/iso"
mkdir -p "$EXTRACT_DIR"

log "=== 2/6 : extraction de l'ISO de base ==="
xorriso -osirrox on -indev "$BASE_ISO" -extract / "$EXTRACT_DIR" >/dev/null
chmod -R u+w "$EXTRACT_DIR"

log "=== 3/6 : injection des fichiers Hyperlite ==="
HL_DIR="$EXTRACT_DIR/hyperlite"
mkdir -p "$HL_DIR/hyperlite-src"

cp "$SCRIPT_DIR/preseed.cfg" "$HL_DIR/preseed.cfg"
cp "$SCRIPT_DIR/partman-auto.sh" "$HL_DIR/partman-auto.sh"
cp "$SCRIPT_DIR/postinstall.sh" "$HL_DIR/postinstall.sh"
cp "$SCRIPT_DIR/ensure-tls-cert.sh" "$HL_DIR/ensure-tls-cert.sh"
cp "$SCRIPT_DIR/write-motd.sh" "$HL_DIR/write-motd.sh"
cp "$SCRIPT_DIR/hyperlite.service" "$HL_DIR/hyperlite.service"
chmod +x "$HL_DIR"/*.sh

# Code applicatif : app/ (backend) + dashboard/dist/ (deja buildee, voir
# `npm run build`) + requirements.txt -- JAMAIS le venv/.git/.env/hyperlite.db
# ni data/ssh|tls|isos de LA MACHINE QUI CONSTRUIT L'ISO : ce sont des
# secrets/donnees d'instance propres a kvm-lab, chaque appliance genere les
# siens au premier demarrage (voir postinstall.sh + ensure-tls-cert.sh).
if [ ! -d "$HYPERLITE_ROOT/dashboard/dist" ]; then
    echo "ERREUR : dashboard/dist introuvable -- lancer 'npm run build' dans dashboard/ avant de construire l'ISO"
    exit 1
fi
rsync -a \
    --exclude venv --exclude .git --exclude .env --exclude hyperlite.db \
    --exclude data/ssh --exclude data/tls --exclude data/isos \
    --exclude installer --exclude '__pycache__' --exclude '*.pyc' \
    --exclude dashboard/node_modules --exclude .claude \
    "$HYPERLITE_ROOT/" "$HL_DIR/hyperlite-src/"

log "=== 3.5/6 : preseed minimal embarque dans l'initrd (langue/clavier) ==="
# Le choix de langue/pays/clavier est demande AVANT que le CD-ROM (et donc
# preseed.cfg via /cdrom/hyperlite/preseed.cfg) ne soit accessible -- ni
# priority=critical ni debian-installer/language=fr en parametre noyau ne
# suffisent a le sauter (teste et confirme en pratique, bloque plusieurs
# minutes sur "[!!] Select a language" sans aucune erreur associee). La
# technique documentee pour des questions aussi precoces est d'embarquer un
# preseed directement DANS l'initrd -- disponible des le tout premier
# instant du boot, avant meme le montage du CD-ROM. Un fichier nomme
# "preseed.cfg" a la RACINE de l'initrd est charge automatiquement par d-i,
# sans parametre noyau necessaire. On l'ajoute par CONCATENATION d'un petit
# cpio+gzip supplementaire a la suite de l'initrd existant (technique
# standard : le noyau deroule des archives cpio concatenees dans l'ordre,
# les fichiers de la derniere archive prevalant sur les precedents) plutot
# que de reconstruire tout l'initrd.
EARLY_PRESEED="$WORKDIR/early-preseed.cfg"
cat > "$EARLY_PRESEED" <<'EOF'
d-i debian-installer/language string fr
d-i debian-installer/country string FR
d-i debian-installer/locale string fr_FR.UTF-8
d-i keyboard-configuration/xkb-keymap select fr
EOF
CPIO_DIR="$WORKDIR/early-preseed-cpio"
mkdir -p "$CPIO_DIR"
cp "$EARLY_PRESEED" "$CPIO_DIR/preseed.cfg"
( cd "$CPIO_DIR" && echo preseed.cfg | cpio -o -H newc 2>/dev/null | gzip -9 > "$WORKDIR/early-preseed.cpio.gz" )
for INITRD in "$EXTRACT_DIR/install.amd/initrd.gz" "$EXTRACT_DIR/install.amd/gtk/initrd.gz"; do
    [ -f "$INITRD" ] && cat "$WORKDIR/early-preseed.cpio.gz" >> "$INITRD"
done

log "=== 4/6 : menu de boot -- installation automatique par defaut ==="
# Meme mecanisme que l'entree "Automated install" deja fournie par Debian
# dans son propre sous-menu "Advanced options" (isolinux/adtxt.cfg,
# boot/grub/grub.cfg) : auto=true priority=critical force toutes les
# reponses debconf a venir du preseed (plus aucune question), quiet masque
# le log noyau verbeux. On y ajoute juste preseed/file pour pointer vers
# notre preseed.cfg embarque, et on rend CETTE entree celle qui demarre
# automatiquement apres un court delai si personne ne touche au clavier --
# la partie que le menu Debian stock n'a pas par defaut (il attend
# indefiniment une touche, sans timeout configure).
# debian-installer/language, /country et /locale (les TROIS -- localechooser
# les traite comme des questions distinctes en interne, preseeder /locale
# seul ne suffit pas toujours a supprimer les deux autres) + keyboard sont
# EGALEMENT passes en param noyau, pas seulement dans preseed.cfg : l'ecran
# de choix de langue est affiche AVANT que le CD-ROM (et donc preseed.cfg)
# ne soit accessible -- priority=critical seul ne suffit pas a le sauter
# sans valeur deja fournie a ce stade (observe en test : bloque sur "Select
# a language" malgre priority=critical + locale preseede). Les parametres
# noyau, eux, sont disponibles des le tout premier instant du boot.
APPEND_ARGS="auto=true priority=critical debian-installer/language=fr debian-installer/country=FR debian-installer/locale=fr_FR.UTF-8 keyboard-configuration/xkb-keymap=fr preseed/file=/cdrom/hyperlite/preseed.cfg hostname=hyperlite domain= --- quiet"

# ---- BIOS (isolinux/syslinux) ----
# Fragment dedie (convention deja utilisee par Debian pour txt.cfg/gtk.cfg/
# adtxt.cfg...) plutot que de modifier txt.cfg en place : plus sur, et on
# n'a pas a parser la seule entree "install" qui s'y trouve.
cat > "$EXTRACT_DIR/isolinux/hyperlite.cfg" <<CFGEOF
label hyperlite-auto
	menu label ^Installer Hyperlite Appliance (automatique)
	menu default
	kernel /install.amd/vmlinuz
	append $APPEND_ARGS
CFGEOF

MENU_CFG="$EXTRACT_DIR/isolinux/menu.cfg"
# menu timeout est en dixiemes de seconde (50 = 5s) ; ontimeout doit
# reprendre exactement le nom du label ci-dessus. gtk.cfg porte
# actuellement "menu default" sur "Graphical install" -- on le retire pour
# que notre entree soit la seule marquee par defaut (comportement de
# syslinux indefini si plusieurs entrees le sont).
sed -i '0,/menu default/{/menu default/d}' "$EXTRACT_DIR/isolinux/gtk.cfg"
sed -i "s/^menu title Debian GNU\/Linux installer menu (BIOS mode)\$/&\nmenu timeout 50\nontimeout hyperlite-auto/" "$MENU_CFG"
sed -i "s/^include stdmenu.cfg\$/include hyperlite.cfg\n&/" "$MENU_CFG"

# ---- UEFI (grub) ----
GRUB_CFG="$EXTRACT_DIR/boot/grub/grub.cfg"
cat > "$WORKDIR/hyperlite-entry-grub.cfg" <<CFGEOF
menuentry 'Installer Hyperlite Appliance (automatique)' {
	set background_color=black
	linux	/install.amd/vmlinuz $APPEND_ARGS
	initrd	/install.amd/initrd.gz
}
CFGEOF
# Ni "set default" ni "set timeout" ne sont definis dans ce grub.cfg d-i --
# on les ajoute explicitement plutot que de compter sur un comportement par
# defaut implicite non documente. Notre entree est inseree en premier
# (index 0) : default=0 pointe donc dessus sans ambiguite.
{
    echo "set default=0"
    echo "set timeout=5"
    echo ""
    cat "$WORKDIR/hyperlite-entry-grub.cfg"
    echo ""
    cat "$GRUB_CFG"
} > "$WORKDIR/grub.cfg.new"
mv "$WORKDIR/grub.cfg.new" "$GRUB_CFG"

log "=== 5/6 : repackaging ISO hybride (BIOS + UEFI) ==="
# Technique standard (celle documentee par xorriso lui-meme et utilisee par
# la plupart des outils de remasterisation d'ISO Debian/Ubuntu) : on
# reconstruit explicitement le catalogue El Torito plutot que de tenter de
# "rejouer" celui de l'ISO source (`-boot_image any replay`, tente en
# premier, echoue : "Cannot enable El Torito boot image... not a data
# file" -- le catalogue original reference des blocs LBA qui n'existent
# plus une fois les fichiers copies sur disque puis reimportes).
#   -isohybrid-mbr : rend l'ISO bootable aussi bien grave sur CD que
#     "dd"-ee brute sur une cle USB (BIOS/MBR).
#   -b/-c isolinux/... : boot BIOS classique (isolinux).
#   -eltorito-alt-boot -e boot/grub/efi.img -isohybrid-gpt-basdat : second
#     catalogue El Torito pour le boot UEFI (image FAT grub deja presente
#     sur l'ISO Debian d'origine), + table de partition GPT pour que l'UEFI
#     la voie aussi en boot USB direct.
rm -f "$OUT_ISO"
xorriso -as mkisofs \
    -r -V "HYPERLITE" \
    -o "$OUT_ISO" \
    -J -joliet-long -cache-inodes \
    -isohybrid-mbr "$ISOHDPFX" \
    -b isolinux/isolinux.bin -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -eltorito-alt-boot \
    -e boot/grub/efi.img -no-emul-boot -isohybrid-gpt-basdat \
    "$EXTRACT_DIR"

log "=== 6/6 : termine ==="
log "ISO : $OUT_ISO ($(du -h "$OUT_ISO" | cut -f1))"
log "Flash : sudo dd if=$OUT_ISO of=/dev/sdX bs=4M status=progress conv=fsync  (ou Rufus/balenaEtcher sous Windows)"
