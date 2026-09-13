#!/bin/bash
# Filet de securite pour la mise a jour Hyperlite depuis Git (chantier 7).
# Lance en processus detache PENDANT que le backend redemarre le service --
# il survit donc au processus Python qui l'a lance (qui, lui, meurt avec le
# restart). Verifie que le nouveau code repond bien sur /health ; si non
# apres plusieurs essais, restaure automatiquement la sauvegarde et
# redemarre a nouveau -- le seul mecanisme capable de rattraper un
# "systemctl restart" qui demarre un code casse (Restart=on-failure dans
# l'unit systemd sinon relance en boucle le MEME code casse indefiniment).
#
# Usage : update_watchdog.sh <chemin_tarball_backup> <repertoire_repo> <log_file>
set -u
BACKUP_TARBALL="$1"
REPO_DIR="$2"
LOG_FILE="$3"

log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG_FILE"; }

log "Watchdog demarre, attente du redemarrage du service..."
sleep 4

OK=0
for i in $(seq 1 10); do
    if curl -sk --max-time 3 https://localhost:8000/health >/dev/null 2>&1; then
        OK=1
        log "Service reponds sur /health (essai $i) -- mise a jour validee, rien a faire."
        break
    fi
    log "Essai $i/10 : /health ne repond pas encore, nouvelle tentative dans 3s."
    sleep 3
done

if [ "$OK" -eq 0 ]; then
    log "ECHEC : le service ne repond toujours pas apres 10 essais -- ROLLBACK automatique."
    if [ -f "$BACKUP_TARBALL" ]; then
        tar -xzf "$BACKUP_TARBALL" -C "$REPO_DIR"
        log "Sauvegarde restauree depuis $BACKUP_TARBALL"
        systemctl restart hyperlite
        log "Service redemarre sur l'ancien code."
    else
        log "ERREUR CRITIQUE : fichier de sauvegarde introuvable ($BACKUP_TARBALL), rollback impossible. Intervention manuelle requise."
    fi
fi
