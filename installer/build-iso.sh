#!/bin/bash
# Builds the bootable Hyperlite ISO: starts from the official Debian netinst image, adds the hyperlite package,
# the offline bundle (every package and wheel the installation needs, so it needs no network), the preseeds and
# the installation scripts, and writes a boot menu with two modes, in French or English: automatic (the default
# after 5 s) and custom, guided step by step.
#
# Usage: ./build-iso.sh [output_iso_path.iso]
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HYPERLITE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_ISO="${1:-$SCRIPT_DIR/hyperlite-appliance-amd64.iso}"
CACHE_DIR="$SCRIPT_DIR/.iso-cache"

# Debian 13 (trixie), the current stable release when this script was written:
# it receives security updates, unlike the archived bookworm (12). Adjust here
# when a newer release ships. Debian publishes point releases regularly (about
# every 2 months) and "current/" on cdimage.debian.org NEVER keeps the old
# ones (the file disappears and the old version returns a 404), so this is NOT
# a pin that stays valid forever: refresh it by hand if this script fails with
# a 404, using
#   curl https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/SHA256SUMS | grep netinst
# to get the real version and SHA256 of the moment.
DEBIAN_VERSION="13.7.0"
ISO_NAME="debian-${DEBIAN_VERSION}-amd64-netinst.iso"
ISO_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/${ISO_NAME}"
ISO_SHA256="a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355"

log() { echo "[build-iso] $*"; }

for bin in xorriso openssl rsync; do
    command -v "$bin" >/dev/null || { echo "ERROR: $bin is required (apt install $bin)"; exit 1; }
done
ISOHDPFX=/usr/lib/ISOLINUX/isohdpfx.bin
[ -f "$ISOHDPFX" ] || { echo "ERROR: the isolinux package is required (apt install isolinux)"; exit 1; }

mkdir -p "$CACHE_DIR"
BASE_ISO="$CACHE_DIR/$ISO_NAME"

log "=== 1/6: base Debian ISO ==="
if [ -f "$BASE_ISO" ] && echo "$ISO_SHA256  $BASE_ISO" | sha256sum -c - >/dev/null 2>&1; then
    log "already cached and verified: $BASE_ISO"
else
    log "downloading: $ISO_URL"
    curl -fL --progress-bar -o "$BASE_ISO" "$ISO_URL"
    echo "$ISO_SHA256  $BASE_ISO" | sha256sum -c -
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT
EXTRACT_DIR="$WORKDIR/iso"
mkdir -p "$EXTRACT_DIR"

log "=== 2/6: extracting the base ISO ==="
xorriso -osirrox on -indev "$BASE_ISO" -extract / "$EXTRACT_DIR" >/dev/null
chmod -R u+w "$EXTRACT_DIR"

log "=== 3/6: injecting the Hyperlite installation files ==="
HL_DIR="$EXTRACT_DIR/hyperlite"
mkdir -p "$HL_DIR"

# The ISO carries the hyperlite package, every Debian package and Python wheel the installation needs (the offline
# bundle below) and the installation scripts: an installation needs no network. The machine is then managed by apt
# like any Debian, from the Debian mirrors and the Hyperlite repository, as soon as it has a network.

# Single source of truth: installer/apt-source.conf. An exported HYPERLITE_APT_URL overrides it,
# for tests only.
if [ -z "${HYPERLITE_APT_URL:-}" ] && [ -f "$SCRIPT_DIR/apt-source.conf" ]; then
    # shellcheck disable=SC1091
    . "$SCRIPT_DIR/apt-source.conf"
fi
if [ -n "${HYPERLITE_APT_URL:-}" ]; then
    echo "HYPERLITE_APT_URL=\"$HYPERLITE_APT_URL\"" > "$HL_DIR/apt-source.conf"
    log "APT repository baked into the ISO: $HYPERLITE_APT_URL"
fi
# Embed the package and the repository public key so the installation does not
# depend on the Hyperlite APT repository being reachable and consistent.
PKG_VERSION=$(cat "$HYPERLITE_ROOT/VERSION")
DEB="$SCRIPT_DIR/hyperlite_${PKG_VERSION}_amd64.deb"
if [ -f "$DEB" ]; then
    cp "$DEB" "$HL_DIR/"
    log "package embedded: $(basename "$DEB")"
else
    log "WARNING: $DEB not found (run installer/build-deb.sh first): the ISO will install from the repository instead"
fi
if [ -f "$SCRIPT_DIR/apt-repo/hyperlite-archive-keyring.asc" ]; then
    cp "$SCRIPT_DIR/apt-repo/hyperlite-archive-keyring.asc" "$HL_DIR/"
fi
{
    echo "version=$(cat "$HYPERLITE_ROOT/VERSION")"
    echo "commit=$(git -C "$HYPERLITE_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "built_at=$(date -u +%FT%TZ)"
} > "$HL_DIR/build-info"
# The preseeds are not copied here: they are embedded in the initrds (step 3.5).
cp "$SCRIPT_DIR/partman-auto.sh" "$HL_DIR/partman-auto.sh"
cp "$SCRIPT_DIR/postinstall.sh" "$HL_DIR/postinstall.sh"
cp "$SCRIPT_DIR/hyperlite-questions.sh" "$HL_DIR/hyperlite-questions.sh"
cp "$SCRIPT_DIR/hyperlite.templates" "$HL_DIR/hyperlite.templates"
cp "$SCRIPT_DIR/packages.list" "$HL_DIR/packages.list"
chmod +x "$HL_DIR"/*.sh

# The offline bundle (installer/build-offline-bundle.sh): every Debian package and Python wheel the installation
# needs, so it runs without a network, as with Proxmox. Without it the installation downloads them.
OFFLINE_BUNDLE="${HYPERLITE_OFFLINE_BUNDLE:-$SCRIPT_DIR/offline-bundle}"
if [ -s "$OFFLINE_BUNDLE/debs/packages.index" ] && [ -s "$OFFLINE_BUNDLE/install.list" ]; then
    rsync -a "$OFFLINE_BUNDLE/" "$HL_DIR/offline/"
    log "offline bundle embedded: $(tr "\n" " " < "$OFFLINE_BUNDLE/bundle-info")($(du -sh "$HL_DIR/offline" | cut -f1))"
else
    log "WARNING: no offline bundle in $OFFLINE_BUNDLE (run installer/build-offline-bundle.sh): the installation will need the network"
fi

log "=== 3.5/6: one initrd per installation mode, with its preseed ==="
# The Debian installer loads ONE preseed file, the first one it finds, and the very first screens (language,
# keyboard) come before the CD is even mounted. So each mode gets its own copy of the installer's initrd with its
# preseed at the root (preseed.cfg + preseed-auto.cfg or preseed-custom.cfg), loaded from the first moment of the
# boot. The automatic mode uses the text installer, the custom one the graphical installer (mouse, clearer
# screens). Debian's own initrds stay untouched for its entries in the advanced menu.
# Injection technique: a small cpio+gzip archive appended to a copy of the initrd (the kernel unpacks concatenated
# cpio archives in order).
HL_INITRD_DIR="$EXTRACT_DIR/install.amd/hyperlite"
mkdir -p "$HL_INITRD_DIR"
for mode in auto custom; do
    CPIO_DIR="$WORKDIR/preseed-$mode"
    mkdir -p "$CPIO_DIR"
    cat "$SCRIPT_DIR/preseed.cfg" "$SCRIPT_DIR/preseed-$mode.cfg" > "$CPIO_DIR/preseed.cfg"
    ( cd "$CPIO_DIR" && echo preseed.cfg | cpio -o -H newc 2>/dev/null | gzip -9 > "$WORKDIR/preseed-$mode.cpio.gz" )
done
cat "$EXTRACT_DIR/install.amd/initrd.gz" "$WORKDIR/preseed-auto.cpio.gz" > "$HL_INITRD_DIR/initrd-auto.gz"
cat "$EXTRACT_DIR/install.amd/gtk/initrd.gz" "$WORKDIR/preseed-custom.cpio.gz" > "$HL_INITRD_DIR/initrd-custom.gz"

log "=== 4/6: boot menu: automatic or custom, in French or English ==="
# The kernel command line answers the first screens, shown before the preseed is read (language, country,
# locale; in automatic mode also the keyboard, the host name and the time zone). debconf/language is the language
# the installer's screens are shown in: answered on the command line, the language step does not set it (tested:
# the screens stayed in English). The preseed at the root of the
# initrd is read in both modes. Automatic: auto=true and priority=critical ask only what is essential. Custom:
# priority=high asks the usual questions of an installation, with the preseed's values as defaults, and no
# auto=true, which skips the keyboard (tested: the keyboard question never came, and stayed empty). hlmode= tells the installation
# scripts which mode runs (installer/hyperlite-questions.sh). hostname=/domain= name the installer's own live
# system, netcfg/* the installed machine.
FR="debian-installer/language=fr debian-installer/country=FR debian-installer/locale=fr_FR.UTF-8 debconf/language=fr"
EN="debian-installer/language=en debian-installer/country=US debian-installer/locale=en_US.UTF-8 debconf/language=en"
AUTO="auto=true priority=critical hlmode=auto netcfg/get_hostname=hyperlite netcfg/hostname=hyperlite netcfg/get_domain=local hostname=hyperlite domain="
CUSTOM="priority=high hlmode=custom"
ARGS_AUTO_FR="$AUTO $FR keyboard-configuration/xkb-keymap=fr time/zone=Europe/Paris --- quiet"
ARGS_AUTO_EN="$AUTO $EN keyboard-configuration/xkb-keymap=us time/zone=Etc/UTC --- quiet"
ARGS_CUSTOM_FR="$CUSTOM $FR --- quiet"
ARGS_CUSTOM_EN="$CUSTOM $EN --- quiet"
INITRD_AUTO=/install.amd/hyperlite/initrd-auto.gz
INITRD_CUSTOM=/install.amd/hyperlite/initrd-custom.gz

# ---- BIOS (isolinux) ----
# isolinux.cfg holds our entries only and boots the automatic French installation after 5 s at the boot: prompt.
# Debian's menu (menu.cfg) is not included: its files carry "default" lines of their own, and the last one read
# wins, so Debian's own default entry booted instead of ours. Nor is vesamenu.c32 loaded: when idle it starts a
# speech synthesis prompt that waits for Enter forever (an accessibility feature configuration cannot switch
# off), which blocked the unattended boot. The text banner lists the entries.
cat > "$EXTRACT_DIR/isolinux/hyperlite-banner.txt" <<'BANNEREOF'


                         HYPERLITE

  Automatic installation in French in 5 seconds if no key is pressed.
  It asks for the root password, then shows the disk it will erase.

  To choose, type a name below at the boot: prompt and press Enter:

    hyperlite             Automatic installation (French)
    hyperlite-custom      Custom installation, guided step by step (French)
    hyperlite-en          Automatic installation (English)
    hyperlite-custom-en   Custom installation, guided step by step (English)
    rescue                Debian rescue mode

BANNEREOF
cat > "$EXTRACT_DIR/isolinux/isolinux.cfg" <<CFGEOF
path
display hyperlite-banner.txt
prompt 1
timeout 50
default hyperlite
label hyperlite
	kernel /install.amd/vmlinuz
	append initrd=$INITRD_AUTO $ARGS_AUTO_FR
label hyperlite-custom
	kernel /install.amd/vmlinuz
	append vga=788 initrd=$INITRD_CUSTOM $ARGS_CUSTOM_FR
label hyperlite-en
	kernel /install.amd/vmlinuz
	append initrd=$INITRD_AUTO $ARGS_AUTO_EN
label hyperlite-custom-en
	kernel /install.amd/vmlinuz
	append vga=788 initrd=$INITRD_CUSTOM $ARGS_CUSTOM_EN
label rescue
	kernel /install.amd/vmlinuz
	append initrd=/install.amd/initrd.gz rescue/enable=true --- quiet
CFGEOF

# ---- UEFI (grub) ----
# Our four entries, then Debian's own menu in a submenu (its rescue mode stays reachable). Debian's grub.cfg
# sets neither default nor timeout: both are set here, and the first entry is the automatic French one.
GRUB_CFG="$EXTRACT_DIR/boot/grub/grub.cfg"
{
    echo "set default=0"
    echo "set timeout=5"
    echo ""
    for entry in \
        "Installer Hyperlite (automatique)|$INITRD_AUTO|$ARGS_AUTO_FR" \
        "Installer Hyperlite (personnalisée, guidée)|$INITRD_CUSTOM|$ARGS_CUSTOM_FR" \
        "Install Hyperlite (automatic)|$INITRD_AUTO|$ARGS_AUTO_EN" \
        "Install Hyperlite (custom, guided)|$INITRD_CUSTOM|$ARGS_CUSTOM_EN"; do
        IFS='|' read -r title initrd args <<< "$entry"
        echo "menuentry '$title' {"
        echo "	set background_color=black"
        echo "	linux	/install.amd/vmlinuz $args"
        echo "	initrd	$initrd"
        echo "}"
    done
    echo ""
    echo "submenu 'Debian installer (advanced, rescue)' {"
    cat "$GRUB_CFG"
    echo "}"
} > "$WORKDIR/grub.cfg.new"
mv "$WORKDIR/grub.cfg.new" "$GRUB_CFG"

log "=== 5/6: repackaging the hybrid ISO (BIOS + UEFI) ==="
# Standard technique (documented by xorriso itself and used by most Debian/
# Ubuntu ISO remastering tools): the El Torito catalog is rebuilt explicitly
# rather than "replayed" from the source ISO (`-boot_image any replay`, tried
# first, fails with "Cannot enable El Torito boot image... not a data file":
# the original catalog references LBA blocks that no longer exist once the
# files have been copied to disk and re-imported).
#   -isohybrid-mbr: makes the ISO bootable both burned on a CD and written raw
#     with "dd" on a USB stick (BIOS/MBR).
#   -b/-c isolinux/...: classic BIOS boot (isolinux).
#   -eltorito-alt-boot -e boot/grub/efi.img -isohybrid-gpt-basdat: second
#     El Torito catalog for UEFI boot (the FAT grub image already present on
#     the original Debian ISO), plus a GPT partition table so that UEFI also
#     sees it when booting directly from USB.
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

log "=== 6/6: done ==="
log "ISO: $OUT_ISO ($(du -h "$OUT_ISO" | cut -f1))"
log "Flash: sudo dd if=$OUT_ISO of=/dev/sdX bs=4M status=progress conv=fsync  (or Rufus/balenaEtcher on Windows)"
