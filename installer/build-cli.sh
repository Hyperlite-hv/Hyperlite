#!/bin/bash
# Builds the `hyperlite` workstation client (cli/) for every supported platform into
# cli/dist/<os>-<arch>/, where the server offers it for download (see
# app/routers/workstation.py). Static binaries (CGO disabled), version stamped from
# VERSION. Needs Go (see cli/go.mod for the minimum version).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=$(cat "$REPO_DIR/VERSION")
OUT="$REPO_DIR/cli/dist"

command -v go >/dev/null || { echo "[build-cli] Go is required (https://go.dev/dl/)" >&2; exit 1; }
rm -rf "$OUT"
cd "$REPO_DIR/cli"
for target in windows/amd64 windows/arm64 linux/amd64 linux/arm64 darwin/amd64 darwin/arm64; do
    os=${target%/*}; arch=${target#*/}
    bin=hyperlite; [ "$os" = windows ] && bin=hyperlite.exe
    mkdir -p "$OUT/$os-$arch"
    CGO_ENABLED=0 GOOS=$os GOARCH=$arch go build -trimpath -ldflags "-s -w -X main.version=$VERSION" -o "$OUT/$os-$arch/$bin" .
    echo "[build-cli] $os-$arch/$bin"
done
