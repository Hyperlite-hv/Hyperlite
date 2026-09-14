#!/bin/bash
# ExecStartPre de hyperlite.service : regenere /etc/issue (bandeau affiche
# sur la console PHYSIQUE avant meme de se logguer) et /etc/motd (affiche
# apres connexion SSH) a chaque demarrage, avec l'IP courante -- l'IP DHCP
# peut changer d'un boot a l'autre, contrairement a un message fige ecrit
# une seule fois a l'installation.
#
# Equivalent fonctionnel du dernier ecran de l'installeur Proxmox VE
# ("Please point your browser to https://IP:8006"), mais tenu a jour a
# chaque redemarrage plutot qu'affiche une seule fois pendant l'install.
set -e

# hostname -I liste IPv4 ET IPv6 sans ordre garanti -- filtre explicitement
# une adresse IPv4 (motif x.x.x.x) plutot que de prendre le premier champ,
# qui peut etre une IPv6 (observe en test : lien-local fec0::... affiche a
# la place de l'IPv4 reellement utile pour se connecter au dashboard).
#
# ExecStartPre demarre des que network-online.target est atteint, mais ce
# target peut se declarer "atteint" avant que le bail DHCP ne soit
# reellement obtenu sur certaines cartes reseau (constate en test sur du
# vrai materiel : bannière affichant "pas encore d'adresse IP" au premier
# boot alors que le reseau finit par fonctionner quelques secondes plus
# tard). On reessaie donc pendant 20s avant d'abandonner, plutot que
# d'echouer sur la toute premiere tentative.
IP=""
for _ in $(seq 1 20); do
    IP=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' | head -1)
    [ -n "$IP" ] && break
    sleep 1
done
IP=${IP:-"(pas encore d'adresse IP -- verifier 'hostname -I' une fois connecte)"}
HOST=$(hostname)

# Le mot de passe est ECRIT EN CLAIR ici plutot que de renvoyer vers
# /root/.hyperlite-initial-password : ce fichier n'est lisible qu'une fois
# CONNECTE en root, ce qui cree un probleme d'oeuf-et-poule (constate en
# test : impossible de se connecter sans le mot de passe, impossible de
# lire le mot de passe sans etre connecte). /etc/issue est deja affiche
# AVANT authentification sur la console physique -- une personne avec un
# acces physique a l'ecran a de toute facon deja franchi la meme barriere
# de confiance qu'un accès root, l'ecrire ici ne l'expose pas davantage.
ROOT_PASS=$(cat /root/.hyperlite-initial-password 2>/dev/null || echo "(voir /root/.hyperlite-initial-password)")

BANNER="
================================================================
  Hyperlite - Bienvenue

  Interface web  : https://${IP}:8000
  Compte         : admin
  Mot de passe   : ${ROOT_PASS}
                   (identique au mot de passe root de cette machine)

  Hote           : ${HOST}
================================================================
"

echo "$BANNER" > /etc/issue
echo "$BANNER" > /etc/motd
