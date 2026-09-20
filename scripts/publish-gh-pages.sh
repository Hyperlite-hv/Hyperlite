#!/bin/bash
# Publishes the generated APT repository to the gh-pages branch.
#
# Usage: publish-gh-pages.sh <apt-repo-dir> <remote-url> <version> <source-commit>
#
# Runs from the post-merge hook, which inherits Git environment variables (GIT_DIR,
# GIT_INDEX_FILE...) from the `git pull` that triggered it. Those variables make every git
# command run in another directory look at the main repository instead, so the mirror commit
# silently missed files (the signed Release, InRelease and Release.gpg stayed stale while
# Packages changed, and apt then reported "File has unexpected size"). This script clears them,
# works in a fresh clone, compares file contents, and verifies the result before returning.
set -eu

unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_PREFIX GIT_OBJECT_DIRECTORY GIT_COMMON_DIR \
    GIT_NAMESPACE GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_QUARANTINE_PATH

SRC="$1"
REMOTE="$2"
VERSION="$3"
SOURCE_COMMIT="$4"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git clone -q --depth 1 --branch gh-pages "$REMOTE" "$WORK/repo"
# --checksum: compare contents, never trust size and modification time (signed files often keep
# the same size from one build to the next).
rsync -a --checksum --delete --exclude=.git "$SRC"/ "$WORK/repo"/

cd "$WORK/repo"
if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -q -m "Automatic publication ${VERSION} (post-merge, commit ${SOURCE_COMMIT})"
    git push -q origin gh-pages
fi

# The published index files must be exactly the ones that were just built and signed.
for f in dists/stable/Release dists/stable/InRelease dists/stable/Release.gpg \
    dists/stable/main/binary-amd64/Packages dists/stable/main/binary-amd64/Packages.gz; do
    if ! git show "HEAD:$f" | cmp -s - "$SRC/$f"; then
        echo "ERROR: $f on gh-pages differs from the freshly built file" >&2
        exit 1
    fi
done
echo "gh-pages mirror verified: index files match the built repository"
