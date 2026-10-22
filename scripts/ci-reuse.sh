#!/bin/bash
# Decides how much of the CI a commit needs (.github/workflows/ci.yml, job `reuse`), so a release does not run the same
# suite four times over the same files: the release PR into test, its push to test, the test -> master PR and its push
# to master each took a full run (about 7 minutes each, queued behind one another).
#
#   full   nothing validated is close enough: every job runs (the default, and the answer to any doubt)
#   exact  a push to test or master whose checks all passed had exactly these files (`git rev-parse HEAD^{tree}`): no
#          job runs again. Identical files are identical inputs: the test -> master PR and the master push of a release
#          have the tree test's tip had.
#   docs   these files differ from such a validated tree only in release files (VERSION, the changelog, docs/,
#          Markdown): the quick jobs run (backend, frontend, shell), the long ones (end-to-end, cfs, package, client)
#          are not run again. The release PR changes nothing else.
#
# A validated tree: the head commit of a run of this workflow on a push to test or master, at most MAX_AGE_DAYS old,
# whose check jobs all passed (skipped counts only because the run itself was a reuse, which validated its tree the
# same way). Push runs only: a pull request run tests a merge commit GitHub does not record in the run. Reading only
# (actions: read); no secret. Any error answers `full`.
#
# Writes mode=... and from=<validated commit> to $GITHUB_OUTPUT (stdout when unset). REPO (owner/name) and GH_TOKEN
# are read by gh.
set -u

MAX_AGE_DAYS=${MAX_AGE_DAYS:-14}
CANDIDATES=${CANDIDATES:-15}
CHECK_JOBS="backend frontend cli shell package cfs e2e"
# The files a release PR changes: anything else is code, tests or packaging, and takes the full suite.
release_file() {
    case "$1" in
        VERSION | CHANGELOG.md | docs/* | *.md) return 0 ;;
        *) return 1 ;;
    esac
}

out=${GITHUB_OUTPUT:-/dev/stdout}
answer() { # <mode> [<from>]
    echo "mode=$1" >> "$out"
    echo "from=${2:-}" >> "$out"
    echo "[ci-reuse] $1${2:+ (validated: ${2:0:12})}"
    exit 0
}

tree=$(git rev-parse 'HEAD^{tree}' 2>/dev/null) || answer full
since=$(date -u -d "-$MAX_AGE_DAYS days" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) || answer full

# Recent successful-or-not push runs of this workflow on the two published branches, newest first.
runs=$(for branch in test master; do
    gh api "repos/$REPO/actions/workflows/ci.yml/runs?branch=$branch&event=push&status=completed&per_page=$CANDIDATES" \
        --jq ".workflow_runs[] | select(.updated_at >= \"$since\") | \"\(.updated_at) \(.id) \(.head_sha)\"" 2>/dev/null
done | sort -r | head -n "$CANDIDATES") || answer full

validated() { # <run id>: every check job passed, or was skipped by a reuse
    local states
    states=$(gh api "repos/$REPO/actions/runs/$1/jobs?per_page=50" \
        --jq '.jobs[] | "\(.name)=\(.conclusion)"' 2>/dev/null) || return 1
    local job state skipped=0
    for job in $CHECK_JOBS; do
        state=$(printf '%s\n' "$states" | sed -n "s/^$job=//p" | head -n 1)
        case "$state" in
            success) ;;
            skipped) skipped=1 ;;
            *) return 1 ;;
        esac
    done
    # The gate itself never skips (if: always()); a job skipped only by a reuse of that run.
    [ "$(printf '%s\n' "$states" | sed -n 's/^e2e=//p' | head -n 1)" = success ] || return 1
    [ "$skipped" = 0 ] || printf '%s\n' "$states" | grep -q '^reuse=success$'
}

docs_from=""
while read -r _ run sha; do
    [ -n "$sha" ] || continue
    validated "$run" || continue
    their=$(gh api "repos/$REPO/git/commits/$sha" --jq .tree.sha 2>/dev/null) || continue
    [ "$their" = "$tree" ] && answer exact "$sha"
    if [ -z "$docs_from" ] && { git cat-file -e "$their" 2>/dev/null || git fetch -q --depth=1 origin "$sha" 2>/dev/null; }; then
        changed=$(git diff --name-only "$their" "$tree" 2>/dev/null) || continue
        only_release=1
        while read -r path; do
            [ -n "$path" ] || continue
            release_file "$path" || { only_release=0; break; }
        done <<< "$changed"
        [ "$only_release" = 1 ] && docs_from=$sha
    fi
done <<< "$runs"

[ -n "$docs_from" ] && answer docs "$docs_from"
answer full
