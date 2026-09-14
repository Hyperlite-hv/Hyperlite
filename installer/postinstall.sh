#!/bin/bash
# Execute CHROOTE dans le systeme cible (via d-i preseed/late_command +
# in-target, voir preseed.cfg) juste apres l'installation de base Debian et
# des paquets listes dans pkgsel/include (libvirt/KVM/python3 deja presents
# a ce stade). Deploie Hyperlite et prepare le premier demarrage.
#
# A ce point : /root/hyperlite-installer contient une copie du contenu de
# installer/ tel qu'embarque dans l'ISO (voir build-iso.sh), y compris
# hyperlite-src/ (le code applicatif : app/, dashboard/dist/, requirements.txt)
# et hyperlite-root-password (mot de passe root genere par partman-auto.sh).
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

log "=== 1/8 : deploiement du code Hyperlite ==="
mkdir -p "$APP_DIR"
cp -r "$INSTALLER_DIR/hyperlite-src/." "$APP_DIR/"
mkdir -p "$APP_DIR/data/isos" "$APP_DIR/data/templates" "$APP_DIR/data/tls" "$APP_DIR/data/ssh"
# ensure-tls-cert.sh/write-motd.sh sont maintenant DANS hyperlite-src/scripts/
# (suivis par git, comme scripts/update_watchdog.sh) -- copies par le cp -r
# ci-dessus, plus besoin de copie separee. ATTENTION (bug reel trouve en
# testant une vraie mise a jour sur le serveur physique d'Antho) : les
# copier separement ici, hors de l'arbre git, les rendait "non suivis" pour
# toujours ("git status --porcelain" affichait "?? scripts/..."), donc
# l'arbre restait "sale" en permanence et le bouton mise a jour restait
# bloque sur TOUTE appliance. Verifie que le rsync a bien conserve le bit
# executable (devrait deja etre le cas, -a le preserve) :
chmod +x "$APP_DIR/scripts/ensure-tls-cert.sh" "$APP_DIR/scripts/write-motd.sh"

# /root en 700 empeche l'utilisateur libvirt-qemu (proprietaire du process
# qemu des VM) de traverser jusqu'a data/isos/*.iso pour les monter comme
# CD-ROM -- bug reel rencontre et corrige sur kvm-lab, applique ici des le
# depart pour ne pas le redecouvrir a la premiere ISO montee.
chmod o+x /root

log "=== 2/8 : environnement Python (venv) ==="
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --no-input --upgrade pip --quiet
"$APP_DIR/venv/bin/pip" install --no-input -r "$APP_DIR/requirements.txt" --quiet

log "=== 3/8 : secrets propres a cette machine ==="
# Chaque appliance genere SES PROPRES secrets (cle JWT, cle SSH d'automation,
# certificat TLS, mot de passe admin) -- rien n'est jamais reutilise depuis
# la machine ayant construit l'ISO. C'est le meme code que sur kvm-lab
# (voir app/core/security.py, SECRET_KEY = env HYPERLITE_SECRET_KEY) : sans
# valeur explicite en .env, une cle aleatoire DIFFERENTE serait regeneree a
# chaque redemarrage du service, deconnectant tous les utilisateurs a
# chaque restart -- on la fixe donc une fois ici, de facon definitive.
SECRET_KEY=$(openssl rand -hex 32)
cat > "$APP_DIR/.env" <<ENVEOF
HYPERLITE_SECRET_KEY=$SECRET_KEY
HYPERLITE_INITIAL_ADMIN_PASSWORD=$ROOT_PASSWORD
ENVEOF
chmod 600 "$APP_DIR/.env"

# Cle SSH d'automation (voir app/core/vm_builder.py::_ensure_automation_keypair)
# : generee ici plutot que laissee a la premiere VM creee, pour eviter
# qu'une image ISO reutilisee sur plusieurs machines ne finisse, par erreur
# de build, avec la meme cle sur toutes -- generation explicite et fraiche a
# chaque installation.
ssh-keygen -t ed25519 -N "" -f "$APP_DIR/data/ssh/hyperlite_automation" -C "hyperlite-automation" -q
chmod 700 "$APP_DIR/data/ssh"
chmod 600 "$APP_DIR/data/ssh/hyperlite_automation"

log "=== 4/8 : mot de passe root Linux ==="
echo "root:$ROOT_PASSWORD" | chpasswd
# Conserve pour affichage post-redemarrage (write-motd.sh) et consultation
# manuelle si besoin -- lisible uniquement par root.
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

log "=== 5/8 : service hyperlite (systemd) ==="
cp "$INSTALLER_DIR/hyperlite.service" /etc/systemd/system/hyperlite.service
systemctl enable hyperlite.service

log "=== 6/8 : libvirt (reseau NAT par defaut) ==="
systemctl enable libvirtd.service
# Le reseau virtuel "default" (NAT, virbr0) est defini par le paquet
# libvirt-daemon-system mais pas toujours demarre/autostart selon la
# distribution -- on le force explicitement pour que les VM aient un reseau
# fonctionnel des le premier demarrage, sans etape manuelle.
virsh net-autostart default 2>/dev/null || true
virsh net-start default 2>/dev/null || true

log "=== 7/8 : MOTD de bienvenue (identifiants + URL, a chaque boot) ==="
# Regenere a chaque demarrage (ExecStartPre, voir hyperlite.service) car
# l'IP DHCP peut changer d'un boot a l'autre -- meme principe que l'ecran
# final de l'installeur Proxmox ("Please point your browser to
# https://IP:8006"), mais tenu a jour dynamiquement plutot qu'affiche une
# seule fois a l'installation.
"$APP_DIR/scripts/write-motd.sh" || true

log "=== 8/8 : nettoyage ==="
# On efface le mot de passe en clair copie depuis l'environnement live, il
# ne subsiste plus qu'en hash (DB Hyperlite) et dans /root/.hyperlite-initial-password
# (meme fichier, but explicite, plutot qu'un fichier au nom generique oublie
# dans un repertoire d'installeur).
shred -u "$INSTALLER_DIR/hyperlite-root-password" 2>/dev/null || rm -f "$INSTALLER_DIR/hyperlite-root-password"
rm -rf "$INSTALLER_DIR/hyperlite-src"

log "postinstall termine -- mot de passe root/admin : $ROOT_PASSWORD"
