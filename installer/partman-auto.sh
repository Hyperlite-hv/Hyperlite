#!/bin/sh
# Run by d-i when the partitioner starts (partman/early_command, preseed-auto.cfg), in automatic mode only.
# Chooses the disk from the hardware actually detected: the first internal disk, with LVM. The CD is mounted by then (it is not when the preseed embedded in the initrd is read, which is why the
# former preseed/include_command never ran this script), and the answers are loaded with debconf-set-selections,
# the same mechanism as a preseed file: it accepts questions whose component is not loaded yet.
#
# It never answers the confirmation that writes the disk: the administrator sees which disk will be erased.
set -e

OUT=/tmp/hyperlite-partman.cfg
log() { echo "[hyperlite-partman] $*" > /dev/console 2>&1 || true; logger -t hyperlite-partman "$*" 2>/dev/null || true; }

# ---- Disk detection ----
# /proc/partitions rather than list-devices (partman-base d-i module): nothing
# guarantees the latter is already loaded at this early stage, whereas
# /proc/partitions is guaranteed to exist in any Linux kernel.
# WHOLE-DISK patterns only (not partitions), per family: sda/vda/xvda/hda (no
# trailing digit), nvme0n1 (no trailing "p1"), mmcblk0 (no trailing "p1", eMMC/SD
# cards of some mini PCs).
#
# Explicitly EXCLUDES REMOVABLE devices (/sys/block/<dev>/removable == 1): on
# real hardware the boot USB stick itself often shows up as a regular SCSI disk
# (/dev/sda), indistinguishable from a real target disk by its name pattern
# alone. Without this filter the script may choose to partition THE USB STICK
# THAT IS BEING BOOTED, as seen on real hardware ("Partition(s) 1, 2 on /dev/sda
# have been written, but we have been unable to inform the kernel... probably
# because it/they are in use", the exact symptom of a disk partitioned while it
# serves as the active boot medium). Virtio/SCSI disks in a TEST environment
# (QEMU) are never flagged removable, so this bug is invisible in a VM and only
# shows up on real hardware.
DISKS=$(awk '
    $4 ~ /^(sd|vd|xvd|hd)[a-z]+$/ { print $4 }
    $4 ~ /^nvme[0-9]+n[0-9]+$/    { print $4 }
    $4 ~ /^mmcblk[0-9]+$/        { print $4 }
' /proc/partitions | {
    result=""
    while read -r dev; do
        removable=$(cat "/sys/block/$dev/removable" 2>/dev/null || echo 0)
        if [ "$removable" != "1" ]; then
            result="${result}${result:+ }$dev"
        else
            log "disk $dev ignored (removable, probably the boot medium)"
        fi
    done
    printf '%s\n' "$result" | tr ' ' '\n' | sed '/^$/d' | sed 's|^|/dev/|'
})
DISK_COUNT=$(printf '%s\n' "$DISKS" | grep -c . || true)
log "detected disks: $DISK_COUNT ($(printf '%s' "$DISKS" | tr '\n' ' '))"

# The system goes on the first internal disk, with LVM; the other disks stay untouched for storage pools. A RAID1
# layout over two disks was tried here, but its recipe failed when the partitioner applied it ("error while setting
# up the preconfigured RAID", tested on two disks): RAID stays a choice of the custom installation (manual
# partitioning) until the automatic one is rebuilt and tested on BIOS and UEFI.
D1=$(printf '%s\n' "$DISKS" | sed -n '1p')
[ -n "$D1" ] || { log "no internal disk found: the installer asks"; exit 0; }
log "LVM on $D1 ($DISK_COUNT internal disk(s), the others are left untouched)"
{
    echo "d-i partman-auto/disk string $D1"
    echo "d-i partman-auto/method string lvm"
    echo "d-i partman-auto/choose_recipe select atomic"
    echo "d-i partman-auto-lvm/guided_size string max"
} > "$OUT"

debconf-set-selections "$OUT"
log "partitioning answers loaded ($DISK_COUNT disk(s))"
