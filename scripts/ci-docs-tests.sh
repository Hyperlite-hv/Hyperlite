#!/bin/bash
# The backend tests a `docs` run needs (scripts/ci-reuse.sh): only release files changed (VERSION, the changelog,
# docs/, Markdown) since a tree whose whole suite passed, so only the tests that read those files run again, not the
# 2,500 others. A test file is in when it names one of them; one added later that reads them is picked up the same
# way. Prints one path per line, relative to the repository's root.
set -eu
cd "$(dirname "$0")/.."
grep -lE '"VERSION"|/ "VERSION"|CHANGELOG\.md|changelog\.d|release-notes|proxmox-parity|README\.md|"docs" /|"docs/[a-z]|\.md"' tests/*.py
