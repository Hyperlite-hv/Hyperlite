#!/bin/bash
# Checks that the public APT mirror is consistent, the way apt sees it: the signed Release
# announces a size and a SHA-256 for every index file, and the files served next to it must match.
# A mismatch is what makes clients fail with "File has unexpected size".
#
# Usage: verify-apt-mirror.sh [--url URL] [--expect-version VERSION] [--wait SECONDS]
#   --url             mirror base URL (default: $HYPERLITE_MIRROR_URL, else installer/apt-source.conf)
#   --expect-version  also require this package version to be listed (detects a stale mirror)
#   --wait            keep retrying for up to SECONDS before giving up (a CDN needs a few minutes)
#
# Exit codes: 0 consistent, 1 inconsistent index files, 2 stale (expected version missing),
# 3 unreachable. When $HYPERLITE_ALERT_WEBHOOK is set, a failure is also posted there as JSON.
#
# Plain requests on purpose, no cache-busting: what matters is what a real client is served.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${HYPERLITE_MIRROR_URL:-}" ] && [ -f "$SCRIPT_DIR/../installer/apt-source.conf" ]; then
    # shellcheck disable=SC1091
    . "$SCRIPT_DIR/../installer/apt-source.conf"
fi
URL="${HYPERLITE_MIRROR_URL:-${HYPERLITE_APT_URL:-}}"
if [ -z "$URL" ]; then
    echo "no mirror URL: pass --url or set HYPERLITE_MIRROR_URL" >&2
    exit 64
fi
EXPECT=""
WAIT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --url) URL="$2"; shift 2 ;;
        --expect-version) EXPECT="$2"; shift 2 ;;
        --wait) WAIT="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done
URL="${URL%/}"
INDEX_FILES="main/binary-amd64/Packages main/binary-amd64/Packages.gz"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

MESSAGE=""

check_once() {
    MESSAGE=""
    if ! curl -fsS --max-time 30 "$URL/dists/stable/Release" -o "$WORK/Release" 2>"$WORK/err"; then
        MESSAGE="cannot fetch $URL/dists/stable/Release: $(head -c 200 "$WORK/err")"
        return 3
    fi
    for f in $INDEX_FILES; do
        # Lines of the SHA256 section look like: " <hash> <size> <path>".
        read -r want_hash want_size < <(awk -v f="$f" '/^SHA256:/ {s=1; next} /^[A-Za-z]/ {s=0} s && $3 == f {print $1, $2}' "$WORK/Release")
        if [ -z "${want_hash:-}" ]; then
            MESSAGE="Release does not list $f"
            return 1
        fi
        if ! curl -fsS --max-time 60 "$URL/dists/stable/$f" -o "$WORK/body" 2>"$WORK/err"; then
            MESSAGE="cannot fetch $f: $(head -c 200 "$WORK/err")"
            return 3
        fi
        got_size="$(stat -c %s "$WORK/body")"
        got_hash="$(sha256sum "$WORK/body" | cut -d' ' -f1)"
        if [ "$got_size" != "$want_size" ] || [ "$got_hash" != "$want_hash" ]; then
            MESSAGE="$f is out of sync: Release announces $want_size bytes, the mirror serves $got_size"
            return 1
        fi
    done
    if [ -n "$EXPECT" ]; then
        curl -fsS --max-time 60 "$URL/dists/stable/main/binary-amd64/Packages" -o "$WORK/Packages" 2>/dev/null
        if ! grep -qx "Version: $EXPECT" "$WORK/Packages"; then
            MESSAGE="the mirror is consistent but does not list version $EXPECT yet"
            return 2
        fi
    fi
    return 0
}

deadline=$(( $(date +%s) + WAIT ))
while :; do
    check_once
    code=$?
    [ "$code" -eq 0 ] && break
    [ "$(date +%s)" -ge "$deadline" ] && break
    sleep 30
done

if [ "$code" -eq 0 ]; then
    echo "[mirror-check $(date -u +%FT%TZ)] OK: $URL is consistent${EXPECT:+ and lists $EXPECT}"
    exit 0
fi

echo "[mirror-check $(date -u +%FT%TZ)] FAILED ($code): $MESSAGE" >&2
if [ -n "${HYPERLITE_ALERT_WEBHOOK:-}" ]; then
    payload="{\"text\": \"Hyperlite APT mirror check failed: ${MESSAGE//\"/\'}\"}"
    curl -fsS --max-time 15 -H 'Content-Type: application/json' -d "$payload" "$HYPERLITE_ALERT_WEBHOOK" >/dev/null 2>&1 || true
fi
exit "$code"
