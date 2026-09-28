#!/bin/bash
# Safety net for the Hyperlite update. Launched as a detached process WHILE the backend restarts the service, so
# it survives the Python process that launched it (which dies with the restart). It checks that the NEW code
# answers on /health; if not within the allowed time, it restores the backup and restarts again. It is the only
# mechanism able to recover from a "systemctl restart" that starts broken code (otherwise Restart=on-failure in
# the systemd unit restarts the SAME broken code in a loop indefinitely).
#
# "The new code answers" means /health reports the version now in <repo>/VERSION: the previous process can keep
# answering for a while as it shuts down (it reports the version it started with), and must not be taken for the
# new one, nor its slow exit for a failure.
#
# Usage: update_watchdog.sh <backup_tarball_path> <repo_directory> <log_file>
set -u
BACKUP_TARBALL="$1"
REPO_DIR="$2"
LOG_FILE="$3"
ATTEMPTS=40   # 3 s apart: about two minutes for the old process to stop and the new one to start
EXPECTED="$(tr -d '[:space:]' < "$REPO_DIR/VERSION" 2>/dev/null || true)"

log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG_FILE"; }

log "Watchdog started, waiting for the service to restart${EXPECTED:+ on $EXPECTED}..."
sleep 4

OK=0
for i in $(seq 1 "$ATTEMPTS"); do
    if BODY="$(curl -sk --max-time 3 https://localhost:8000/health 2>/dev/null)"; then
        GOT="$(printf '%s' "$BODY" | sed -n 's/.*"hyperlite_version": *"\([^"]*\)".*/\1/p')"
        if [ -z "$EXPECTED" ] || [ "$GOT" = "$EXPECTED" ]; then
            OK=1
            log "Service answers on /health${GOT:+ with $GOT} (attempt $i): update validated, nothing to do."
            break
        fi
        log "Attempt $i/$ATTEMPTS: /health still answers with ${GOT:-an unknown version}, the previous process has not stopped yet."
    else
        log "Attempt $i/$ATTEMPTS: /health does not answer yet, retrying in 3s."
    fi
    sleep 3
done

if [ "$OK" -eq 0 ]; then
    log "FAILURE: the new code still does not answer after $ATTEMPTS attempts: automatic ROLLBACK."
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
