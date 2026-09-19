#!/bin/sh
# Run by d-i preseed/include_command (NOT early_command, see preseed.cfg).
# IMPORTANT, counter-intuitive: the standard output of include_command is NOT
# re-read as preseed content directly. It must contain the PATH OF A FILE to
# include, exactly like preseed/include (tested: printing preseed content
# directly on stdout makes d-i try to fetch a file named after the FIRST WORD
# of the output, e.g. "d-i", hence the observed error "could not retrieve
# file:///cdrom/hyperlite/d-i"). The content is therefore written to a
# temporary file and ONLY its path is printed.
#
# Why include_command anyway (rather than early_command + a direct db_set): the
# included file is loaded by the SAME mechanism as preseed.cfg itself, which
# accepts values for components that are not loaded yet (e.g. partman-auto),
# unlike a direct db_set from early_command, which fails ("question doesn't
# exist", return code 10, tested and confirmed) as long as the owning component
# has not been loaded by the installer at that very early stage.
#
# Also generates the partitioning strategy (RAID1+LVM with 2+ disks, plain LVM otherwise) from the hardware
# actually detected.
set -e

OUT=/tmp/hyperlite-dynamic-preseed.cfg
log() { echo "[hyperlite-partman] $*" > /dev/console 2>&1 || true; }

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

# Partition suffix: /dev/sda -> /dev/sda2, but /dev/nvme0n1 -> /dev/nvme0n1p2.
partsuffix() {
    case "$1" in
        *[0-9]) printf 'p' ;;
    esac
}

if [ "$DISK_COUNT" -ge 2 ]; then
    D1=$(printf '%s\n' "$DISKS" | sed -n '1p')
    D2=$(printf '%s\n' "$DISKS" | sed -n '2p')
    log "RAID1 mode: $D1 + $D2"
    S1=$(partsuffix "$D1")
    S2=$(partsuffix "$D2")

    {
        # No passwd/root-password* here: see preseed.cfg (moved there because
        # include_command is consumed before the passwd/user-setup udeb is
        # loaded).
        echo "d-i partman-auto/disk string $D1 $D2"
        echo "d-i partman-auto/method string raid"
        echo "d-i partman-lvm/device_remove_lvm boolean true"
        echo "d-i partman-md/device_remove_md boolean true"
        echo "d-i partman-lvm/confirm boolean true"
        echo "d-i partman-lvm/confirm_nooverwrite boolean true"
        # Heredoc with a QUOTED delimiter ('EOF'): no shell substitution, and a
        # trailing "\" stays literal (a continuation read by the PRESEED
        # PARSER, not by the shell), so $iflabel/$reusemethod need no escaping.
        cat <<'EOF'
d-i partman-auto/expert_recipe string                       \
      multiraid ::                                          \
              538 538 1075 free                              \
                      $iflabel{ gpt } $reusemethod{ }        \
                      method{ efi } format{ }                 \
              .                                              \
              1000 10000 1000000000 raid                     \
                      $iflabel{ gpt } $reusemethod{ }        \
                      method{ raid }                          \
              .
EOF
        echo "d-i partman-auto/choose_recipe select multiraid"
        echo "d-i partman-auto-raid/recipe string  1 2 0 ext4 /  ${D1}${S1}2#${D2}${S2}2  ."
        # The system still boots when the RAID is degraded (a failed disk)
        # rather than dropping to an unreachable rescue shell: a hypervisor that
        # is up in degraded mode beats one that is unreachable.
        echo "d-i mdadm/boot_degraded boolean true"
    } > "$OUT"
else
    D1=$(printf '%s\n' "$DISKS" | sed -n '1p')
    log "single disk + LVM mode: $D1"

    {
        # No passwd/root-password* here: see preseed.cfg (same reason as in the
        # RAID1 branch above).
        echo "d-i partman-auto/disk string $D1"
        echo "d-i partman-auto/method string lvm"
        echo "d-i partman-auto/choose_recipe select atomic"
        echo "d-i partman-auto-lvm/guided_size string max"
        echo "d-i partman-lvm/confirm boolean true"
        echo "d-i partman-lvm/confirm_nooverwrite boolean true"
    } > "$OUT"
fi

log "preseed fragment generated ($DISK_COUNT disk(s)) -> $OUT"
echo "$OUT"
