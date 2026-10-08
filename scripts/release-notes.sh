#!/bin/bash
# Writes the release notes of one version where the update dialog reads them (GET /update/check), next to the
# APT repository: <dir>/<version>.en.md from its CHANGELOG.md section, <dir>/<version>.fr.md from
# docs/release-notes/fr/<version>.md, and <dir>/index.json, the published versions that have notes, newest first.
#
# Only semver releases (1.0.0 and later) have notes; a dated version is skipped. A release without a CHANGELOG
# section fails: the release PR writes it (docs/design/updates-1.0.md).
#
# Usage: release-notes.sh <version> <notes directory>
set -euo pipefail

VERSION="$1"
OUT="$2"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(~[a-z0-9.]+)?$ ]]; then
    echo "[release-notes] $VERSION is not a semver release: no notes"
    exit 0
fi
# A test build (1.1.0~test.20261004.1530) shows the notes being written for the coming release.
BASE="${VERSION%%~*}"
mkdir -p "$OUT"

# The section "## [1.0.1] - 2026-10-20" up to the next "## [".
awk -v v="$BASE" '
    /^## \[/ { if (found) exit; if (index($0, "## [" v "]") == 1) { found = 1; next } }
    found { print }
' "$REPO_DIR/CHANGELOG.md" > "$OUT/$VERSION.en.md"
if ! grep -q '[^[:space:]]' "$OUT/$VERSION.en.md"; then
    if [ "$BASE" != "$VERSION" ]; then
        # Between two releases the coming one has no section yet: the test channel shows what is unreleased.
        awk '/^## \[/ { if (found) exit; if (index($0, "## [Unreleased]") == 1) { found = 1; next } } found { print }' \
            "$REPO_DIR/CHANGELOG.md" > "$OUT/$VERSION.en.md"
    else
        echo "[release-notes] CHANGELOG.md has no section for $BASE: write it in the release PR" >&2
        rm -f "$OUT/$VERSION.en.md"
        exit 1
    fi
fi
if [ -f "$REPO_DIR/docs/release-notes/fr/$BASE.md" ]; then
    cp "$REPO_DIR/docs/release-notes/fr/$BASE.md" "$OUT/$VERSION.fr.md"
fi

python3 - "$OUT" <<'PY'
import json, pathlib, re, sys
out = pathlib.Path(sys.argv[1])

def key(v):
    main, _, pre = v.partition("~")
    return (tuple(int(x) for x in main.split(".")), pre == "", pre)

versions = sorted({p.name[: -len(".en.md")] for p in out.glob("*.en.md")}, key=key, reverse=True)
index = [{"version": v, "fr": (out / f"{v}.fr.md").exists()} for v in versions]
(out / "index.json").write_text(json.dumps(index, indent=1) + "\n")
PY
echo "[release-notes] notes of $VERSION written to $OUT"
