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

# ensure-tls-cert.sh/write-motd.sh ne sont PLUS copies ici depuis
# installer/ (bug reel trouve en testant une vraie mise a jour sur le
# serveur physique d'Antho) : postinstall.sh les recopiait separement dans
# $APP_DIR/scripts/, un chemin JAMAIS ajoute au commit git initial --
# "git status" les voyait donc comme fichiers non suivis ("??"), rendant
# l'arbre "sale" en PERMANENCE et bloquant le bouton mise a jour sur TOUTE
# appliance installee depuis cet ISO, definitivement (jusqu'a une premiere
# mise a jour manuelle qui les aurait fait disparaitre du diff, mais
# jamais avant). Corrige a la racine : ces deux scripts vivent maintenant
# dans scripts/ (comme update_watchdog.sh deja suivi par git), donc inclus
# automatiquement par le rsync de hyperlite-src/ ci-dessous et commites
# des le premier commit -- plus besoin de copie separee ni cote build-iso.sh
# ni cote postinstall.sh.
cp "$SCRIPT_DIR/preseed.cfg" "$HL_DIR/preseed.cfg"
cp "$SCRIPT_DIR/partman-auto.sh" "$HL_DIR/partman-auto.sh"
cp "$SCRIPT_DIR/postinstall.sh" "$HL_DIR/postinstall.sh"
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

log "=== 3.6/6 : initialisation d'un depot Git dans le code embarque ==="
# Sans ca, le bouton "Verifier les mises a jour" (chantier 7, git fetch/pull)
# ne fonctionne PAS sur une appliance installee depuis cet ISO : sans .git,
# ce n'est pas un depot -- il fallait jusqu'ici reconstruire et reflasher un
# ISO entier a chaque nouvelle version. Un `git clone` complet de
# l'historique n'est PAS utilise ici (ISO plus volumineuse, et l'historique
# de developpement de kvm-lab n'a pas a etre distribue avec chaque
# appliance) : un commit UNIQUE representant l'etat exact du code embarque
# suffit -- app/routers/update.py fait un `git reset --hard origin/<branche>`
# a la mise a jour, qui ne depend PAS d'un historique commun avec le depot
# distant (contrairement a un merge/rebase). Premiere mise a jour seulement :
# le "changelog" affiche avant application peut etre vide/non significatif
# (historique local et distant disjoints tant qu'aucune vraie mise a jour
# n'a encore ete faite depuis cette appliance) -- limite connue, sans
# consequence sur le mecanisme d'application lui-meme.
(
    cd "$HL_DIR/hyperlite-src"
    git init -q
    git config user.email "appliance@hyperlite.local"
    git config user.name "Hyperlite Appliance Builder"
    git remote add origin "https://github.com/twikles/hyperlite.git"
    git add -A
    git commit -q -m "Instantane embarque dans l'ISO appliance (base pour les mises a jour ulterieures via /update/check)"
)
log "depot Git initialise ($(cd "$HL_DIR/hyperlite-src" && git rev-parse --short HEAD))"

log "=== 3.5/6 : preseed embarque directement dans l'initrd ==="
# CAUSE RACINE (trouvee en inspectant /var/log/installer/syslog sur une VRAIE
# machine installee, apres plusieurs bugs d'apparence non lies) : d-i charge
# UN SEUL fichier de preseed, le premier trouve, et s'arrete la -- il ne
# charge PAS en plus celui pointe par le parametre noyau "preseed/file=".
# Historique de cette decouverte : on a d'abord cru a une succession de
# problemes de timing independants ("telle ou telle question posee avant que
# le CD-ROM ne soit accessible") et duplique un a un les elements bloquants
# (langue, reseau, mot de passe, partman, apt-setup, miroir...) dans un
# preseed minimal embarque au tout debut de l'initrd -- CE MECANISME
# fonctionnait, mais chaque "correctif" masquait en fait le vrai probleme
# sans jamais le reveler, puisqu'il couvrait de plus en plus de questions
# sans qu'on remarque que le fichier CD (/cdrom/hyperlite/preseed.cfg,
# preseed/include_command, preseed/late_command) n'etait JAMAIS lu. Preuve
# definitive : "grep late_command /var/log/installer/syslog" -> 0 resultat
# apres une installation qui s'est pourtant terminee sans un seul ecran
# bloque -- Hyperlite lui-meme n'avait jamais ete deploye (late_command,
# seul point d'entree de postinstall.sh, n'avait jamais tourne). Le syslog
# confirmait : "preseed: successfully loaded preseed file from
# file:///preseed.cfg" -- notre fichier embarque dans l'initrd, jamais celui
# du CD-ROM.
# CORRECTIF DEFINITIF : ne plus reconstruire un sous-ensemble de directives
# a la main ici, mais embarquer le VRAI preseed.cfg (celui du depot, avec
# include_command/late_command) directement a la racine de l'initrd -- une
# seule source de verite, garantie chargee des le tout premier instant du
# boot (avant meme le montage du CD-ROM). Les chemins "/cdrom/hyperlite/..."
# a l'interieur de include_command/late_command restent valables : ce sont
# des scripts EXECUTES bien plus tard (quand le CD est deja monte), pas des
# fichiers de preseed a charger -- seul le CHARGEMENT du preseed lui-meme
# doit se faire depuis l'initrd, pas l'execution des scripts qu'il declenche.
# Technique d'injection : concatenation d'un petit cpio+gzip a la suite de
# l'initrd existant (le noyau deroule des archives cpio concatenees dans
# l'ordre, les fichiers de la derniere archive prevalant sur les precedents)
# plutot que de reconstruire tout l'initrd.
CPIO_DIR="$WORKDIR/early-preseed-cpio"
mkdir -p "$CPIO_DIR"
cp "$SCRIPT_DIR/preseed.cfg" "$CPIO_DIR/preseed.cfg"
( cd "$CPIO_DIR" && echo preseed.cfg | cpio -o -H newc 2>/dev/null | gzip -9 > "$WORKDIR/early-preseed.cpio.gz" )
for INITRD in "$EXTRACT_DIR/install.amd/initrd.gz" "$EXTRACT_DIR/install.amd/gtk/initrd.gz"; do
    [ -f "$INITRD" ] && cat "$WORKDIR/early-preseed.cpio.gz" >> "$INITRD"
done

log "=== 4/6 : menu de boot -- installation automatique par defaut ==="
# Meme mecanisme que l'entree "Automated install" deja fournie par Debian
# dans son propre sous-menu "Advanced options" (isolinux/adtxt.cfg,
# boot/grub/grub.cfg) : auto=true priority=critical force toutes les
# reponses debconf a venir du preseed (plus aucune question, deja charge
# depuis l'initrd a l'etape precedente), quiet masque le log noyau verbeux.
# On rend CETTE entree celle qui demarre automatiquement apres un court
# delai si personne ne touche au clavier --
# la partie que le menu Debian stock n'a pas par defaut (il attend
# indefiniment une touche, sans timeout configure).
# debian-installer/language, /country et /locale (les TROIS -- localechooser
# les traite comme des questions distinctes en interne, preseeder /locale
# seul ne suffit pas toujours a supprimer les deux autres) + keyboard +
# netcfg/get_hostname/hostname/get_domain sont EGALEMENT passes en param
# noyau, en plus d'etre dans le preseed.cfg desormais embarque dans
# l'initrd ci-dessus : les tout premiers ecrans (langue, clavier) sont
# traites avant meme que cdebconf ait fini de charger le preseed
# initrd-embarque, donc seuls des parametres noyau (disponibles depuis le
# tout premier instant du boot, avant tout chargement de fichier) les
# sautent de maniere fiable -- prudence gardee malgre le correctif racine
# ci-dessus, plutot que de re-decouvrir ce cas particulier plus tard.
# hostname=/domain= (params noyau Linux generiques) restent aussi presents
# en plus des netcfg/* ci-dessus : ils fixent le nom de la machine EN COURS
# D'INSTALLATION (environnement live), pas la reponse aux questions debconf
# netcfg/* qui pilotent le nom PERSISTE sur la machine cible -- deux choses
# differentes malgre le nom similaire.
# PAS de "preseed/file=/cdrom/..." ici (contrairement aux versions
# precedentes de ce script) : confirme non fonctionnel, voir le commentaire
# "CAUSE RACINE" au-dessus -- ce parametre n'etait jamais consulte de toute
# facon puisque le preseed initrd-embarque est trouve et charge en premier.
APPEND_ARGS="auto=true priority=critical debian-installer/language=fr debian-installer/country=FR debian-installer/locale=fr_FR.UTF-8 keyboard-configuration/xkb-keymap=fr netcfg/get_hostname=hyperlite netcfg/hostname=hyperlite netcfg/get_domain=local hostname=hyperlite domain= --- quiet"

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
# ATTENTION (bug reel trouve en testant un vrai boot, pas visible a l'oeil) :
# la ligne "menu title ... Debian GNU/Linux installer menu (BIOS mode)"
# contient un octet BEL (0x07) invisible entre "title" et "Debian" --
# beacon utilise par Debian pour le bip d'accessibilite. Un sed cherchant la
# chaine litterale "menu title Debian..." ne matche donc JAMAIS (0 remplacement,
# aucune erreur), et menu timeout/ontimeout ne sont jamais injectes : le menu
# reste alors bloque sur le comportement Debian par defaut (probe de synthese
# vocale apres ~15s, qui attend indefiniment un Entree -- jamais d'installation
# automatique). D'ou le ".*" qui absorbe cet octet au lieu de le matcher en dur.
sed -i '0,/menu default/{/menu default/d}' "$EXTRACT_DIR/isolinux/gtk.cfg"
sed -i "s/^menu title.*BIOS mode).*\$/&\nmenu timeout 50\nontimeout hyperlite-auto/" "$MENU_CFG"
# Ci-dessus : suffisant pour que l'entree soit surlignee/par defaut SI un
# humain regarde l'ecran et navigue le menu vesamenu manuellement. PAS
# suffisant pour l'automatique (bug reel trouve en testant plusieurs vrais
# boots) : "menu timeout"/"ontimeout" ne pilotent PAS le prompt "Press a
# key, otherwise speech synthesis will be started in N seconds..." qu'on
# voit toujours apparaitre et tourner a son propre rythme (~15s) quoi qu'on
# mette dans menu.cfg (teste : reaffirmer notre timeout/ontimeout tout en
# bas du fichier, apres spkgtk.cfg/spk.cfg, n'a RIEN change). Ce prompt est
# une fonctionnalite d'accessibilite CABLEE EN DUR dans le binaire
# vesamenu.c32 lui-meme (volontairement non contournable par simple config,
# pour ne jamais pouvoir etre coupee par erreur pour un utilisateur
# malvoyant) -- elle se declenche des qu'on est inactif dans vesamenu,
# independamment de toute valeur de timeout. Contournement fiable : ne PAS
# passer par vesamenu.c32 du tout pour le chemin automatique. isolinux.cfg
# (le fichier de premier niveau, charge avant meme menu.cfg) pointe
# aujourd'hui "default" sur vesamenu.c32 avec un timeout de 0 (demarrage
# immediat du menu graphique) -- on le fait pointer directement sur notre
# noyau a la place, avec un vrai delai. menu.cfg/vesamenu restent
# accessibles manuellement (appuyer sur une touche puis taper "vesamenu")
# pour qui veut vraiment naviguer le menu graphique, mais ne sont plus sur
# le chemin du demarrage automatique -- donc plus jamais atteints sans
# interaction humaine explicite.
# Banniere texte affichee au tout premier ecran de boot (mecanisme isolinux
# "display", independant de vesamenu -- ne declenche PAS le bug
# d'accessibilite ci-dessus) : rend le choix automatique/manuel VISIBLE des
# le depart plutot que cache derriere un prompt "boot:" vide ou il faudrait
# deviner quoi taper (demande explicite d'Antho : la possibilite de choisir
# doit etre visible des le debut, pas seulement techniquement presente).
# ATTENTION (bug reel retrouve en testant) : "prompt 1" (au lieu de "prompt
# 0" ci-dessous) reintroduit le blocage d'accessibilite du tout debut de ce
# fichier -- ce n'est PAS specifiquement vesamenu.c32 qui le declenche, mais
# le simple fait que le prompt "boot:" soit affiche/actif des le depart. Le
# fichier "display" ci-dessus, lui, s'affiche immediatement (avant meme le
# debut du compte a rebours) SANS activer cet etat -- il reste donc
# visible en permanence sans jamais risquer le blocage, tant que "prompt"
# reste a 0.
cat > "$EXTRACT_DIR/isolinux/hyperlite-banner.txt" <<'BANNEREOF'


                         HYPERLITE APPLIANCE

  Demarrage automatique dans 5 secondes (installation complete de
  Hyperlite, sans aucune interaction) si vous n'appuyez sur aucune touche.

  Pour choisir manuellement, tapez un nom ci-dessous au prompt "boot:"
  puis Entree :

    hyperlite-auto    Installation Hyperlite (identique a l'automatique)
    install           Installation Debian standard (manuelle, texte)
    installgui        Installation Debian standard (manuelle, graphique)

BANNEREOF
ISOLINUX_CFG="$EXTRACT_DIR/isolinux/isolinux.cfg"
cat > "$ISOLINUX_CFG" <<CFGEOF
path
display hyperlite-banner.txt
prompt 0
timeout 50
default hyperlite-auto
label hyperlite-auto
	kernel /install.amd/vmlinuz
	append $APPEND_ARGS
include menu.cfg
CFGEOF
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
