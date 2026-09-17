#!/bin/bash
# Execute CHROOTE dans le systeme cible (via d-i preseed/late_command +
# in-target, voir preseed.cfg) juste apres l'installation de base Debian et
# des paquets listes dans pkgsel/include (libvirt/KVM/python3/curl/gnupg
# deja presents a ce stade).
#
# REECRIT le 2026-09-17 (chantier "apt install comme Proxmox") : Hyperlite
# n'est plus embarque en source dans l'ISO (plus de hyperlite-src/, plus de
# `git init`) -- installe directement via le VRAI depot APT publie
# (https://twikles.github.io/hyperlite/, voir installer/build-apt-repo.sh),
# exactement comme le ferait un admin qui tape la commande a la main. Une
# appliance fraiche est donc NATIVEMENT geree par apt des le premier
# demarrage -- plus besoin de reconstruire/reflasher un ISO entier a chaque
# nouvelle version (l'objectif d'origine du chantier 7, enfin atteint
# proprement).
set -e

log() { echo "[hyperlite-postinstall] $*"; }

INSTALLER_DIR=/root/hyperlite-installer
APP_DIR=/root/hyperlite
# Repli sur la constante "hyperlite" si le fichier est absent/vide : ce
# fichier est ecrit par partman-auto.sh (preseed/include_command), un
# mecanisme dont la fiabilite s'est averee incertaine pour d'autres valeurs
# (voir preseed.cfg) -- vu que le mot de passe est de toute facon fixe
# ("hyperlite", pas aleatoire), pas de raison de laisser tout postinstall.sh
# echouer ici (set -e) si ce fichier venait a manquer.
ROOT_PASSWORD=$(cat "$INSTALLER_DIR/hyperlite-root-password" 2>/dev/null || echo "hyperlite")

log "=== 1/6 : dépôt APT Hyperlite ==="
# BUG REEL trouve en testant une vraie installation (comportement standard
# et documente de l'installeur Debian, pas specifique a ce depot) :
# l'installeur ajoute AUTOMATIQUEMENT le CD-ROM d'installation comme
# source APT dans /etc/apt/sources.list -- `apt-get update` echoue alors
# globalement avec "Le depot cdrom://... n'a pas de fichier Release" MEME
# SI toutes les autres sources (dont la notre) sont recuperees avec
# succes, puisqu'apt renvoie un code d'erreur des qu'UNE SEULE source
# echoue, peu importe laquelle. Retire cette ligne avant tout apt-get
# update -- plus besoin du CD-ROM comme source une fois les depots
# Debian standards + le notre configures.
sed -i '/^deb cdrom:/d' /etc/apt/sources.list

# BUG REEL trouve et DIAGNOSTIQUE A FOND en testant une vraie installation
# sur serveur-antho (2026-09-17) : `apt-get update` echouait de facon
# PERSISTANTE (confirme sur 30 essais repartis sur plus de 10 minutes,
# TOUJOURS le meme ecart) avec "Le fichier a une taille incoherente" en
# pointant vers le depot GitHub Pages -- PAS une fenetre de propagation
# transitoire comme suppose au debut : verifie EN INTERROGEANT DIRECTEMENT
# LA MACHINE HOTE (serveur-antho, pas la VM de test, plus de 1h20 apres la
# derniere publication) qu'InRelease et Packages restaient incoherents
# entre eux. GitHub Pages est un CDN multi-nœuds SANS garantie de
# coherence forte entre plusieurs fichiers lies publies dans le meme
# commit -- ce n'est pas un defaut ponctuel corrigible par un budget de
# retry, aussi genereux soit-il, c'est une propriete structurelle de cette
# infrastructure pour ce cas d'usage precis (verifier des checksums entre
# fichiers separes).
#
# FIX ROBUSTE (pas un contournement) : plutot que de continuer a esperer
# qu'un budget de retry toujours plus grand finisse par suffire, le depot
# est servi directement depuis kvm-lab (nginx, port 8899, lie uniquement a
# son IP Tailscale 100.88.184.24 -- jamais expose sur l'internet public)
# -- aucun CDN entre l'origine et le client, coherence garantie par
# construction (un seul fichier sur disque, jamais deux copies
# desynchronisees). Toutes les machines concernees (kvm-lab, serveur-antho,
# les appliances qu'Antho deploie lui-meme) sont deja sur ce meme reseau
# Tailscale. Le depot public GitHub Pages (https://twikles.github.io/hyperlite)
# reste publie en parallele (utile hors Tailscale, ex. une distribution a
# des tiers plus tard) mais n'est plus la source utilisee ici.
curl -fsSL http://100.88.184.24:8899/hyperlite-archive-keyring.asc | gpg --dearmor -o /usr/share/keyrings/hyperlite-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hyperlite-archive-keyring.gpg] http://100.88.184.24:8899 stable main" > /etc/apt/sources.list.d/hyperlite.list
apt-get update || { log "ERREUR : apt-get update a échoué (kvm-lab injoignable en Tailscale ?)"; exit 1; }

log "=== 2/6 : installation d'Hyperlite (apt install hyperlite) ==="
# Le postinst du paquet (installer/deb/postinst) fait tout le travail
# applicatif lui-meme : cree le venv, installe requirements.txt, genere des
# secrets propres a CETTE machine (.env absent = premiere installation),
# installe/active/demarre le service systemd. Rien de plus a faire ici pour
# l'application elle-meme.
DEBIAN_FRONTEND=noninteractive apt-get install -y hyperlite

log "=== 3/6 : mot de passe root Linux + admin Hyperlite (alignés, simples) ==="
# Demande explicite d'Antho POUR L'APPLIANCE ISO precisement : un mot de
# passe simple et IDENTIQUE pour la toute premiere connexion (root Linux et
# admin Hyperlite), a changer immediatement apres -- volontairement pas
# aleatoire, pour une premiere prise en main facile façon "boote, attends,
# c'est installe".
echo "root:$ROOT_PASSWORD" | chpasswd

# BUG REEL trouve en testant une vraie installation (2026-09-17) : la
# premiere version de cette etape faisait un UPDATE SQL direct sur la
# table users, en supposant qu'elle existait deja (creee par le postinst
# du paquet .deb qui demarre le service) -- mais DANS LE CHROOT
# d'installation, systemctl est bride par policy-rc.d ("Running in
# chroot, ignoring command 'start'", comportement standard et VOULU de
# Debian pendant une installation, pas un bug corrigible) : le service
# n'a donc jamais reellement demarre, et la base de donnees (schema +
# compte admin, crees par seed_admin() au premier demarrage de l'appli)
# n'existe pas encore a ce stade -- "sqlite3.OperationalError: no such
# table: users". Corrige en appelant seed_admin() (app/core/seed.py)
# directement ICI plutot que d'attendre un demarrage qui ne peut pas
# avoir lieu dans ce contexte : deja idempotent (ne fait rien si un admin
# existe deja -- sans consequence si le vrai premier demarrage, plus tard
# hors chroot, l'appelle a nouveau) et lit deja
# HYPERLITE_INITIAL_ADMIN_PASSWORD depuis l'environnement -- exactement
# le mecanisme prevu pour ce cas precis, cree la DB ET le compte admin
# avec le bon mot de passe en une seule operation.
HYPERLITE_INITIAL_ADMIN_PASSWORD="$ROOT_PASSWORD" "$APP_DIR/venv/bin/python3" -c "
import sys; sys.path.insert(0, '$APP_DIR')
from app.core.seed import seed_admin
seed_admin()
"
echo "$ROOT_PASSWORD" > /root/.hyperlite-initial-password
chmod 600 /root/.hyperlite-initial-password

# Par defaut, Debian n'autorise PAS le login root par mot de passe en SSH
# (seulement par cle, PermitRootLogin=prohibit-password implicite) -- sans
# ce fichier, le mot de passe affiche par write-motd.sh ne permettrait pas
# de se connecter en SSH, seulement en console locale. Meme reglage que
# kvm-lab (verifie : PermitRootLogin yes + PasswordAuthentication yes).
cat > /etc/ssh/sshd_config.d/hyperlite.conf <<'EOF'
PermitRootLogin yes
PasswordAuthentication yes
EOF

# nfs-kernel-server (chantier 26) : le paquet Debian l'active par defaut a
# l'installation (verifie reellement en testant sur kvm-lab), ce qui
# exposerait un service NFS en ecoute sur chaque appliance meme sans aucun
# export configure -- desactive ici, un nœud qui veut vraiment servir du
# NFS le reactivera lui-meme au moment de configurer un export (pas geree
# par l'UI Hyperlite pour l'instant, seul le CLIENT netfs l'est).
systemctl disable nfs-server.service 2>/dev/null || true

log "=== 4/6 : libvirt (réseau NAT par défaut) ==="
systemctl enable libvirtd.service
# Le reseau virtuel "default" (NAT, virbr0) est defini par le paquet
# libvirt-daemon-system mais pas toujours demarre/autostart selon la
# distribution -- on le force explicitement pour que les VM aient un reseau
# fonctionnel des le premier demarrage, sans etape manuelle.
virsh net-autostart default 2>/dev/null || true
virsh net-start default 2>/dev/null || true

log "=== 5/6 : redémarrage (mot de passe/MOTD à jour) ==="
# Le service a deja ete demarre UNE fois par le postinst du paquet
# (etape 2 ci-dessus), avec l'ancien mot de passe admin aleatoire -- un
# redemarrage regenere le MOTD (ExecStartPre, voir hyperlite.service) avec
# le mot de passe DEFINITIF ecrit a l'etape 3, pour que le bandeau de
# bienvenue affiche la bonne valeur des le premier login.
systemctl restart hyperlite.service

log "=== 6/6 : nettoyage ==="
# On efface le mot de passe en clair copie depuis l'environnement live, il
# ne subsiste plus qu'en hash (DB Hyperlite) et dans /root/.hyperlite-initial-password
# (meme fichier, but explicite, plutot qu'un fichier au nom generique oublie
# dans un repertoire d'installeur).
shred -u "$INSTALLER_DIR/hyperlite-root-password" 2>/dev/null || rm -f "$INSTALLER_DIR/hyperlite-root-password"

log "terminé"
