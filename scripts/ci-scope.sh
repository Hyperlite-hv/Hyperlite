#!/bin/bash
# Which of the long jobs of the CI (.github/workflows/ci.yml) a pull request needs, from the files it changes, so a
# change to the client, to hyperlite-cfs or to the documentation does not wait for the end-to-end suite, and a change
# to the dashboard does not build hyperlite-cfs under three sanitizers.
#
#   e2e      the end-to-end suite: anything but cfs/, cli/, docs/, lab/, installer/, the unit tests and Markdown
#   cfs      hyperlite-cfs (cfs/) and its Python client (app/core/cfs_client.py)
#   cli      the workstation client (cli/, installer/build-cli.sh)
#   package  the runtime and the package: anything but the dashboard, docs/, lab/, cli/, the unit tests and Markdown
#
# The quick jobs (backend, frontend, shell) always run. A push to test or master needs everything (the release reuse
# of scripts/ci-reuse.sh trusts only a push run where every job ran), as does a change to the CI itself, a pull
# request with too many files to list, and any error: the answer to a doubt is to run the job. Reading only
# (pull-requests: read); no secret.
#
# Writes e2e=, cfs=, cli=, package= (true or false) to $GITHUB_OUTPUT (stdout when unset). EVENT (the event name), PR
# (the pull request's number), REPO (owner/name) and GH_TOKEN are read.
set -u

out=${GITHUB_OUTPUT:-/dev/stdout}
answer() { # <e2e> <cfs> <cli> <package>
    printf 'e2e=%s\ncfs=%s\ncli=%s\npackage=%s\n' "$1" "$2" "$3" "$4" >> "$out"
    echo "[ci-scope] e2e=$1 cfs=$2 cli=$3 package=$4"
    exit 0
}
everything() { answer true true true true; }

[ "${EVENT:-}" = pull_request ] && [ -n "${PR:-}" ] || everything
# A renamed file counts under both of its names. The API lists at most 3000 files.
files=$(gh api --paginate "repos/$REPO/pulls/$PR/files?per_page=100" \
    --jq '.[] | .filename, (.previous_filename // empty)' 2> /dev/null) || everything
[ -n "$files" ] || everything
[ "$(printf '%s\n' "$files" | wc -l)" -lt 3000 ] || everything

e2e=false cfs=false cli=false package=false
while read -r f; do
    [ -n "$f" ] || continue
    case "$f" in
        .github/workflows/ci.yml | .github/actions/* | scripts/ci-scope.sh | scripts/ci-reuse.sh) everything ;;
    esac
    case "$f" in
        cfs/* | app/core/cfs_client.py) cfs=true ;;
    esac
    case "$f" in
        cli/* | installer/build-cli.sh) cli=true ;;
    esac
    case "$f" in
        tests/test_replication_qemu.py | tests/conftest.py) e2e=true ;; # run by the end-to-end job
        cfs/* | cli/* | docs/* | lab/* | installer/* | tests/* | changelog.d/* | .github/* | *.md | LICENSE | VERSION) ;;
        *) e2e=true ;;
    esac
    case "$f" in
        dashboard/* | docs/* | lab/* | cli/* | tests/* | changelog.d/* | .github/* | *.md | LICENSE) ;;
        *) package=true ;;
    esac
done <<< "$files"
answer "$e2e" "$cfs" "$cli" "$package"
