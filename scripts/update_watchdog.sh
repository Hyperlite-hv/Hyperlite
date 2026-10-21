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
# The rollback stops the service first, restores the code (Git: back to the previous commit, so the working tree
# matches HEAD again and later updates are not refused as "not clean"), then extracts the archive. The archive holds
# no database: the live one is never overwritten (the previous code runs on the newer schema, which only grows), and
# a consistent copy taken before the update sits next to the archive (<archive>.db) for a manual restore.
#
# Usage: update_watchdog.sh <backup_tarball_path> <repo_directory> <log_file> [<previous git commit>]
#
# From 1.0.2 (docs/design/privilege-separation.md, section 9) the updater starts a copy of this script kept out of
# the package (dpkg may remove the original while it runs) and hands it, in the environment:
#   HYPERLITE_EXPECTED_VERSION  the version the new code must report (the new package may not ship
#                               <repo_directory>/VERSION, when it moves the files);
#   HYPERLITE_PREVIOUS_DEB      the kept package of the version replaced: the rollback reinstalls it instead of
#                               extracting the archive, which works whatever the layout of either version.
set -u
BACKUP_TARBALL="$1"
REPO_DIR="$2"
LOG_FILE="$3"
PREVIOUS_COMMIT="${4:-}"
ATTEMPTS=40   # 3 s apart: about two minutes for the old process to stop and the new one to start
EXPECTED="${HYPERLITE_EXPECTED_VERSION:-$(tr -d '[:space:]' < "$REPO_DIR/VERSION" 2>/dev/null || true)}"
PREVIOUS_DEB="${HYPERLITE_PREVIOUS_DEB:-}"

# The log must never stop a rollback: a version that moves the files can take the log's directory with it (seen on a
# test node: the rollback's apt-get, redirected to a log in a directory just moved, never ran). Without its
# directory, the log goes next to this script, and a write that still fails is ignored.
if [ ! -d "$(dirname -- "$LOG_FILE")" ]; then
    LOG_FILE="$(dirname -- "$(readlink -f -- "$0")")/watchdog.log"
fi
log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG_FILE" 2>/dev/null || true; }
run_logged() { # a command whose output goes to the log when it can: the command runs whatever happens to the log
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    printf '%s\n' "$out" >> "$LOG_FILE" 2>/dev/null || true
    return "$rc"
}

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
    if [ -n "$PREVIOUS_DEB" ] && [ -f "$PREVIOUS_DEB" ]; then
        # The package of the version replaced, installed again: its own postinst puts its files and units back
        # (and, from 1.0.2, the files a newer version moved: scripts/migrate_layout.sh back).
        systemctl stop hyperlite
        if run_logged env HYPERLITE_SKIP_RESTART=1 DEBIAN_FRONTEND=noninteractive \
            apt-get install -y --allow-downgrades "$PREVIOUS_DEB"; then
            log "Previous version reinstalled from $PREVIOUS_DEB"
        else
            log "CRITICAL ERROR: reinstalling $PREVIOUS_DEB failed. Manual intervention required."
        fi
        systemctl start hyperlite
        log "Service started on the previous version."
    elif [ -f "$BACKUP_TARBALL" ]; then
        # Nothing may run on the files being put back.
        systemctl stop hyperlite
        if [ -n "$PREVIOUS_COMMIT" ] && [ -d "$REPO_DIR/.git" ]; then
            if git -C "$REPO_DIR" reset --hard "$PREVIOUS_COMMIT" >> "$LOG_FILE" 2>&1; then
                log "Code set back to commit $PREVIOUS_COMMIT."
            else
                log "WARNING: git reset to $PREVIOUS_COMMIT failed: the working tree may differ from HEAD."
            fi
        fi
        # The archive is rooted at the directory NAME (tar -C <parent> <name>), so it must be
        # extracted from the PARENT: extracting inside $REPO_DIR nests a copy and restores nothing.
        tar -xzf "$BACKUP_TARBALL" -C "$(dirname "$REPO_DIR")"
        log "Backup restored from $BACKUP_TARBALL (the database was left as it is; copy from before the update: ${BACKUP_TARBALL%.tar.gz}.db)"
        # The built interface is not in the archive (derived): rebuild it for the restored code when the tools are there.
        if [ -n "$PREVIOUS_COMMIT" ] && [ -d "$REPO_DIR/dashboard/node_modules" ] && command -v npm >/dev/null 2>&1; then
            if (cd "$REPO_DIR/dashboard" && timeout 600 npm run build) >> "$LOG_FILE" 2>&1; then
                log "Interface rebuilt for the previous code."
            else
                log "WARNING: rebuilding the interface failed: it may not match the restored code."
            fi
        fi
        systemctl start hyperlite
        log "Service started on the previous code."
    else
        log "CRITICAL ERROR: backup file not found ($BACKUP_TARBALL), rollback impossible. Manual intervention required."
    fi
fi
