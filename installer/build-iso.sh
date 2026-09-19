#!/bin/bash
# Builds the bootable "Hyperlite Appliance" ISO: starts from the official
# Debian netinst image, injects the preseed and the installation scripts, and
# changes the boot menu so that the unattended installation starts by default
# after a short delay (boot, wait, done).
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

# The ISO embeds NO application code, only the installation scripts. Hyperlite
# itself is installed by postinstall.sh through `apt install hyperlite` from the
# published APT repository, exactly as an administrator would do by hand, so a
# fresh appliance is natively managed by apt from its first boot. Updates
# therefore never require rebuilding or reflashing an ISO, and the ISO stays
# small (no source code or Git history to embed).
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
cp "$SCRIPT_DIR/preseed.cfg" "$HL_DIR/preseed.cfg"
cp "$SCRIPT_DIR/partman-auto.sh" "$HL_DIR/partman-auto.sh"
cp "$SCRIPT_DIR/postinstall.sh" "$HL_DIR/postinstall.sh"
chmod +x "$HL_DIR"/*.sh

log "=== 3.5/6: preseed embedded directly in the initrd ==="
# The Debian installer (d-i) loads ONE preseed file, the first one it finds,
# and stops there: it does NOT also load the one referenced by the
# "preseed/file=" kernel parameter. The file on the CD
# (/cdrom/hyperlite/preseed.cfg, and with it preseed/include_command and
# preseed/late_command) would therefore never be read if another preseed were
# found first. Evidence: "grep late_command /var/log/installer/syslog" returns
# nothing after an installation that nevertheless completed without a single
# blocking screen, which means postinstall.sh (reached only through
# late_command) never ran.
# So this script embeds the REAL preseed.cfg of the repository (including
# include_command/late_command) at the root of the initrd: a single source of
# truth, loaded from the very first moment of the boot (before the CD-ROM is
# even mounted). The "/cdrom/hyperlite/..." paths inside
# include_command/late_command remain valid: they point to scripts EXECUTED
# much later (once the CD is mounted), not to preseed files to load.
# Injection technique: append a small cpio+gzip archive to the existing initrd
# (the kernel unpacks concatenated cpio archives in order, and files from the
# last archive win over the previous ones) rather than rebuilding the initrd.
CPIO_DIR="$WORKDIR/early-preseed-cpio"
mkdir -p "$CPIO_DIR"
cp "$SCRIPT_DIR/preseed.cfg" "$CPIO_DIR/preseed.cfg"
( cd "$CPIO_DIR" && echo preseed.cfg | cpio -o -H newc 2>/dev/null | gzip -9 > "$WORKDIR/early-preseed.cpio.gz" )
for INITRD in "$EXTRACT_DIR/install.amd/initrd.gz" "$EXTRACT_DIR/install.amd/gtk/initrd.gz"; do
    [ -f "$INITRD" ] && cat "$WORKDIR/early-preseed.cpio.gz" >> "$INITRD"
done

log "=== 4/6: boot menu, unattended installation by default ==="
# Same mechanism as the "Automated install" entry that Debian already ships in
# its own "Advanced options" submenu (isolinux/adtxt.cfg, boot/grub/grub.cfg):
# auto=true priority=critical forces every upcoming debconf answer from the
# preseed (no more questions), and quiet hides the verbose kernel log. THIS
# entry becomes the one that starts automatically after a short delay when
# nobody touches the keyboard, which the stock Debian menu does not do (it
# waits indefinitely for a key, with no timeout configured).
# debian-installer/language, /country and /locale (all THREE, because
# localechooser handles them as distinct questions and preseeding /locale alone
# is not always enough to suppress the other two), the keyboard and
# netcfg/get_hostname/hostname/get_domain are ALSO passed as kernel
# parameters, in addition to being in the preseed.cfg embedded in the initrd:
# the very first screens (language, keyboard) are processed before cdebconf has
# finished loading the embedded preseed, so only kernel parameters (available
# from the very first moment of the boot) skip them reliably.
# hostname=/domain= (generic Linux kernel parameters) are also present in
# addition to netcfg/*: they set the name of the machine BEING INSTALLED (the
# live environment), not the answers to the netcfg/* debconf questions that
# drive the name PERSISTED on the target machine. These are two different
# things despite the similar name.
# No "preseed/file=/cdrom/..." here: it is never consulted anyway, because the
# preseed embedded in the initrd is found and loaded first (see above).
# The installer locale defaults (fr/FR/fr_FR.UTF-8) are the appliance defaults.

# ---- BIOS (isolinux/syslinux) ----
# Dedicated fragment (the convention Debian already uses for
# txt.cfg/gtk.cfg/adtxt.cfg...) rather than editing txt.cfg in place: safer,
# and no need to parse the single "install" entry found there.
cat > "$EXTRACT_DIR/isolinux/hyperlite.cfg" <<CFGEOF
label hyperlite-auto
	menu label ^Install Hyperlite Appliance (automatic)
	menu default
	kernel /install.amd/vmlinuz
	append $APPEND_ARGS
CFGEOF

MENU_CFG="$EXTRACT_DIR/isolinux/menu.cfg"
# menu timeout is in tenths of a second (50 = 5 s); ontimeout must repeat the
# label name above exactly. gtk.cfg currently carries "menu default" on
# "Graphical install": it is removed so that our entry is the only one marked
# as default (syslinux behaviour is undefined when several entries are).
# WARNING: the "menu title ... Debian GNU/Linux installer menu (BIOS mode)"
# line contains an invisible BEL byte (0x07) between "title" and "Debian",
# which Debian uses for the accessibility beep. A sed looking for the literal
# string "menu title Debian..." therefore NEVER matches (0 replacements, no
# error), and menu timeout/ontimeout are never injected: the menu then stays on
# the default Debian behaviour (a speech synthesis probe after ~15 s, which
# waits indefinitely for Enter, so no unattended installation ever happens).
# Hence the ".*", which absorbs this byte instead of matching it literally.
sed -i '0,/menu default/{/menu default/d}' "$EXTRACT_DIR/isolinux/gtk.cfg"
sed -i "s/^menu title.*BIOS mode).*\$/&\nmenu timeout 50\nontimeout hyperlite-auto/" "$MENU_CFG"
# The above is enough for the entry to be highlighted/default IF a human looks
# at the screen and navigates the vesamenu manually. It is NOT enough for the
# unattended path: "menu timeout"/"ontimeout" do NOT drive the "Press a key,
# otherwise speech synthesis will be started in N seconds..." prompt, which
# always shows up and runs at its own pace (~15 s) whatever menu.cfg says
# (tested: re-asserting our timeout/ontimeout at the very bottom, after
# spkgtk.cfg/spk.cfg, changed NOTHING). This prompt is an accessibility feature
# HARD-WIRED in the vesamenu.c32 binary itself (deliberately not bypassable by
# configuration, so that it can never be switched off by mistake for a
# visually impaired user): it triggers as soon as vesamenu is idle,
# independently of any timeout value.
# Reliable workaround: do NOT go through vesamenu.c32 at all for the
# unattended path. isolinux.cfg (the top-level file, loaded even before
# menu.cfg) currently points "default" at vesamenu.c32 with a timeout of 0
# (immediate start of the graphical menu): it is made to point directly at
# our kernel instead, with a real delay. menu.cfg/vesamenu stay reachable
# manually (press a key, then type "vesamenu") for anyone who really wants to
# browse the graphical menu, but are no longer on the unattended boot path, so
# they are never reached without an explicit human interaction.
# Text banner displayed on the very first boot screen (the isolinux "display"
# mechanism, independent of vesamenu, which does NOT trigger the accessibility
# problem above): it makes the automatic/manual choice VISIBLE from the start
# instead of hiding it behind an empty "boot:" prompt where one would have to
# guess what to type.
# WARNING: "prompt 1" (instead of the "prompt 0" below) brings back the
# accessibility block described at the top of this comment. It is not
# vesamenu.c32 specifically that triggers it, but the mere fact that the
# "boot:" prompt is displayed/active from the start. The "display" file above
# is shown immediately (before the countdown even begins) WITHOUT enabling that
# state, so it stays visible permanently without ever risking the block, as
# long as "prompt" stays at 0.
cat > "$EXTRACT_DIR/isolinux/hyperlite-banner.txt" <<'BANNEREOF'


                         HYPERLITE APPLIANCE

  Automatic start in 5 seconds (full Hyperlite installation, with no
  interaction) if you press no key.

  To choose manually, type a name below at the "boot:" prompt and press
  Enter:

    hyperlite-auto    Hyperlite installation (same as the automatic one)
    install           Standard Debian installation (manual, text)
    installgui        Standard Debian installation (manual, graphical)

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
menuentry 'Install Hyperlite Appliance (automatic)' {
	set background_color=black
	linux	/install.amd/vmlinuz $APPEND_ARGS
	initrd	/install.amd/initrd.gz
}
CFGEOF
# Neither "set default" nor "set timeout" is defined in this d-i grub.cfg: we
# add them explicitly rather than relying on an undocumented implicit default.
# Our entry is inserted first (index 0), so default=0 points at it without
# ambiguity.
{
    echo "set default=0"
    echo "set timeout=5"
    echo ""
    cat "$WORKDIR/hyperlite-entry-grub.cfg"
    echo ""
    cat "$GRUB_CFG"
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
