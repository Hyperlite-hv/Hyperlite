#!/bin/sh
# Execute par d-i preseed/include_command (PAS early_command -- voir
# preseed.cfg). IMPORTANT, contre-intuitif : la sortie standard de
# include_command n'est PAS relue comme du contenu preseed directement --
# elle doit contenir le CHEMIN D'UN FICHIER a inclure, exactement comme
# preseed/include (teste en pratique : imprimer le contenu preseed
# directement sur stdout fait que d-i essaie de recuperer un fichier nomme
# d'apres le PREMIER MOT de la sortie, ex. "d-i", d'ou l'erreur observee
# "could not retrieve file:///cdrom/hyperlite/d-i"). On ecrit donc le
# contenu dans un fichier temporaire et on n'imprime QUE son chemin.
#
# Interet de include_command malgre tout (plutot que early_command +
# db_set direct) : le fichier ainsi inclus est charge par le MEME mecanisme
# que preseed.cfg lui-meme, qui accepte des valeurs pour des composants pas
# encore charges (ex. partman-auto) -- contrairement a un db_set direct
# depuis early_command, qui echoue ("question doesn't exist", code retour
# 10, teste et confirme en pratique) tant que le composant proprietaire n'a
# pas encore ete charge par l'installeur a ce stade tres precoce.
#
# Genere aussi le mot de passe root aleatoire (affiche en fin d'installation,
# voir postinstall.sh) et la strategie de partitionnement (RAID1+LVM si 2+
# disques, LVM simple sinon) selon le materiel reellement detecte.
set -e

OUT=/tmp/hyperlite-dynamic-preseed.cfg
log() { echo "[hyperlite-partman] $*" > /dev/console 2>&1 || true; }

# ---- Mot de passe root aleatoire ----
# En clair (pas -crypted) : le composant passwd du vrai installeur se charge
# du hachage lui-meme, aucune dependance a mkpasswd/openssl necessaire ici.
ROOT_PASS=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20)
echo "$ROOT_PASS" > /tmp/hyperlite-root-password
log "mot de passe root genere"

# ---- Detection des disques ----
# /proc/partitions plutot que list-devices (module d-i partman-base) : rien
# ne garantit que ce dernier soit deja charge a ce stade precoce, alors que
# /proc/partitions est garanti present dans n'importe quel noyau Linux.
# Motifs de DISQUE ENTIER uniquement (pas une partition) par famille :
# sda/vda/xvda/hda (pas de chiffre final), nvme0n1 (pas de "p1" final),
# mmcblk0 (pas de "p1" final, cartes eMMC/SD de certains mini-PC).
#
# EXCLUT explicitement les peripheriques AMOVIBLES (/sys/block/<dev>/removable
# == 1) -- sur du vrai materiel, la cle USB de demarrage elle-meme apparait
# souvent comme un disque SCSI classique (/dev/sda), indiscernable d'un vrai
# disque cible par le seul motif de nom. Sans ce filtre, le script peut
# choisir de partitionner LA CLE USB EN COURS DE DEMARRAGE -- constate en
# test sur du vrai materiel ("Partition(s) 1, 2 on /dev/sda have been
# written, but we have been unable to inform the kernel... probably because
# it/they are in use", symptome exact d'un disque partitionne pendant qu'il
# sert de support de boot actif). Les disques virtio/scsi en environnement
# de TEST (QEMU) ne sont eux jamais marques removable, d'ou ce bug invisible
# en VM et uniquement rencontre sur du vrai materiel.
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
            log "disque $dev ignore (amovible -- probablement le support de boot)"
        fi
    done
    printf '%s\n' "$result" | tr ' ' '\n' | sed '/^$/d' | sed 's|^|/dev/|'
})
DISK_COUNT=$(printf '%s\n' "$DISKS" | grep -c . || true)
log "disques detectes : $DISK_COUNT ($(printf '%s' "$DISKS" | tr '\n' ' '))"

# Suffixe de partition : /dev/sda -> /dev/sda2, mais /dev/nvme0n1 -> /dev/nvme0n1p2.
partsuffix() {
    case "$1" in
        *[0-9]) printf 'p' ;;
    esac
}

if [ "$DISK_COUNT" -ge 2 ]; then
    D1=$(printf '%s\n' "$DISKS" | sed -n '1p')
    D2=$(printf '%s\n' "$DISKS" | sed -n '2p')
    log "mode RAID1 : $D1 + $D2"
    S1=$(partsuffix "$D1")
    S2=$(partsuffix "$D2")

    {
        echo "d-i passwd/root-password password $ROOT_PASS"
        echo "d-i passwd/root-password-again password $ROOT_PASS"
        echo "d-i partman-auto/disk string $D1 $D2"
        echo "d-i partman-auto/method string raid"
        echo "d-i partman-lvm/device_remove_lvm boolean true"
        echo "d-i partman-md/device_remove_md boolean true"
        echo "d-i partman-lvm/confirm boolean true"
        echo "d-i partman-lvm/confirm_nooverwrite boolean true"
        # Heredoc a delimiteur QUOTE ('EOF') : aucune substitution shell,
        # "\" en fin de ligne reste litteral (continuation lue par le
        # PARSEUR PRESEED, pas par le shell) -- $iflabel/$reusemethod n'ont
        # donc pas besoin d'etre echappes ici.
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
        # Le systeme demarre quand meme si le RAID est degrade (un disque en
        # panne) plutot que de tomber sur un shell de secours inaccessible :
        # mieux vaut un hyperviseur up en mode degrade qu'injoignable.
        echo "d-i mdadm/boot_degraded boolean true"
    } > "$OUT"
else
    D1=$(printf '%s\n' "$DISKS" | sed -n '1p')
    log "mode disque unique + LVM : $D1"

    {
        echo "d-i passwd/root-password password $ROOT_PASS"
        echo "d-i passwd/root-password-again password $ROOT_PASS"
        echo "d-i partman-auto/disk string $D1"
        echo "d-i partman-auto/method string lvm"
        echo "d-i partman-auto/choose_recipe select atomic"
        echo "d-i partman-auto-lvm/guided_size string max"
        echo "d-i partman-lvm/confirm boolean true"
        echo "d-i partman-lvm/confirm_nooverwrite boolean true"
    } > "$OUT"
fi

log "fragment preseed genere ($DISK_COUNT disque(s)) -> $OUT"
echo "$OUT"
