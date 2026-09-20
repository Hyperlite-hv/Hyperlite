#!/bin/bash
# Safety net for the Hyperlite update from Git. Launched as a detached process
# WHILE the backend restarts the service, so it survives the Python process that
# launched it (which dies with the restart). It checks that the new code answers
# on /health; if not after several attempts, it automatically restores the backup
# and restarts again. It is the only mechanism able to recover from a
# "systemctl restart" that starts broken code (otherwise Restart=on-failure in the
# systemd unit restarts the SAME broken code in a loop indefinitely).
#
# Usage: update_watchdog.sh <backup_tarball_path> <repo_directory> <log_file>
set -u
BACKUP_TARBALL="$1"
REPO_DIR="$2"
LOG_FILE="$3"

log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG_FILE"; }

log "Watchdog started, waiting for the service to restart..."
sleep 4

OK=0
for i in $(seq 1 10); do
    if curl -sk --max-time 3 https://localhost:8000/health >/dev/null 2>&1; then
        OK=1
        log "Service answers on /health (attempt $i): update validated, nothing to do."
        break
    fi
    log "Attempt $i/10: /health does not answer yet, retrying in 3s."
    sleep 3
done

if [ "$OK" -eq 0 ]; then
    log "FAILURE: the service still does not answer after 10 attempts: automatic ROLLBACK."
    if [ -f "$BACKUP_TARBALL" ]; then
        # The archive is rooted at the directory NAME (tar -C <parent> <name>), so it must be
        # extracted from the PARENT: extracting inside $REPO_DIR nests a copy and restores nothing.
        tar -xzf "$BACKUP_TARBALL" -C "$(dirname "$REPO_DIR")"
        log "Backup restored from $BACKUP_TARBALL"
        systemctl restart hyperlite
        log "Service restarted on the previous code."
    else
        log "CRITICAL ERROR: backup file not found ($BACKUP_TARBALL), rollback impossible. Manual intervention required."
    fi
fi
