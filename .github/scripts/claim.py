#!/usr/bin/env python3
"""Who works on what: claim an issue before working on it, release it when done.

Several assistants work on this repository at once, some of them under the same GitHub account, so an assignee
cannot say which one holds a task. A claim says it in the issue itself: the label `in-progress`, the label of the
owner's assistant (`claude-antho`, `claude-nico`, `claude-terraform`), and a block at the top of the issue's description naming the
owner, the session and the files it will touch. The comparison page (docs/proxmox-parity.html, /parity/) reads the
same block to show the board, and `check` finds who already holds the files a new task needs.

    python3 .github/scripts/claim.py list
    python3 .github/scripts/claim.py check app/core/libvirt_utils.py app/routers/vms/
    python3 .github/scripts/claim.py take 330 --owner antho --session terraform --files app/core/cluster_view.py
    python3 .github/scripts/claim.py release 330 --note "merged in #560"

The token comes from GH_TOKEN or GITHUB_TOKEN, or the file named by --token-file. Standard library only.
"""

import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO = "Hyperlite-hv/Hyperlite"
API = f"https://api.github.com/repos/{REPO}"
LABEL = "in-progress"
# The people whose assistants work on the repository: each has a label, `claude-<owner>`.
OWNERS = ("antho", "nico", "terraform")
# The page parses this same marker: keep both in step (tests/test_claim.py checks it).
MARKER = "<!-- claim "
BLOCK = re.compile(r"<!-- claim (\{.*?\}) -->.*?<!-- /claim -->\n*", re.S)
# A claim with no activity on its issue for this long shows as stale: anyone may ask to take it over.
STALE_DAYS = 3


def parse(body):
    """The claim recorded in an issue description, or None."""
    m = BLOCK.search(body or "")
    if not m:
        return None
    try:
        claim = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(claim, dict) or not claim.get("owner") or not claim.get("session"):
        return None
    claim["files"] = [f for f in claim.get("files") or [] if isinstance(f, str)]
    return claim


def render(claim, number):
    """The block written at the top of the description: the machine-readable line, then the same for people."""
    files = ", ".join(f"`{f}`" for f in claim["files"]) or "not given"
    return (
        f"{MARKER}{json.dumps(claim, separators=(',', ':'))} -->\n"
        f"> **In progress**: {claim['owner']}'s assistant, session `{claim['session']}`, since {claim['since']}. "
        f"Files: {files}.\n"
        f"> Release it with `python3 .github/scripts/claim.py release {number}`.\n"
        "<!-- /claim -->\n\n"
    )


def strip(body):
    return BLOCK.sub("", body or "", count=1)


def overlaps(a, b):
    """Two paths touch the same files: equal, or one is a directory holding the other."""
    a, b = a.strip("/"), b.strip("/")
    return a == b or b.startswith(a + "/") or a.startswith(b + "/")


def conflicts(files, claims, skip=None):
    """The claims (other than issue `skip`) that hold one of `files`, as (number, claim, [paths])."""
    found = []
    for number, claim in claims:
        if number == skip:
            continue
        hit = sorted({held for held in claim["files"] for f in files if overlaps(held, f)})
        if hit:
            found.append((number, claim, hit))
    return found


def same_holder(a, b):
    return a["owner"] == b["owner"] and a["session"] == b["session"]


class GitHub:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, data=None):
        req = urllib.request.Request(  # noqa: S310 -- API is a fixed https URL
            API + path,
            method=method,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Accept": "application/vnd.github+json", "User-Agent": "hyperlite-claim"},
        )
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"token {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 -- API is a fixed https URL
                return json.load(r) if r.status != 204 else None
        except urllib.error.HTTPError as e:
            sys.exit(f"GitHub answered {e.code} to {method} {path}: {e.read().decode(errors='replace')[:300]}")

    def claims(self):
        """Every open claimed issue, as (number, claim, issue)."""
        out = []
        for issue in self.call("GET", f"/issues?labels={LABEL}&state=open&per_page=100"):
            claim = parse(issue.get("body"))
            if claim:
                out.append((issue["number"], claim, issue))
        return out


def stale(issue, now):
    updated = datetime.datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
    return (now - updated).days >= STALE_DAYS


def cmd_list(gh, args):
    now = datetime.datetime.now(datetime.UTC)
    rows = gh.claims()
    if not rows:
        print("No issue is claimed.")
    for number, claim, issue in sorted(rows, key=lambda r: r[0]):
        mark = "  [stale]" if stale(issue, now) else ""
        print(f"#{number} {issue['title']}{mark}")
        print(
            f"    {claim['owner']} / {claim['session']} since {claim['since']}; files: {', '.join(claim['files']) or '-'}"
        )


def cmd_check(gh, args):
    found = conflicts(args.paths, [(n, c) for n, c, _ in gh.claims()])
    for number, claim, hit in found:
        print(f"#{number} held by {claim['owner']} / {claim['session']}: {', '.join(hit)}")
    if not found:
        print("Nobody holds these files.")
    return 1 if found else 0


def cmd_take(gh, args):
    issue = gh.call("GET", f"/issues/{args.issue}")
    if issue.get("pull_request") or issue["state"] != "open":
        sys.exit(f"#{args.issue} is not an open issue: claim the issue the work is for.")
    files = [f.strip() for f in args.files.split(",") if f.strip()] if args.files else []
    claim = {
        "owner": args.owner,
        "session": args.session,
        "since": datetime.date.today().isoformat(),
        "files": files,
    }
    held = parse(issue.get("body"))
    if held and same_holder(held, claim):
        claim["since"] = held["since"]
    elif held and not args.force:
        sys.exit(
            f"#{args.issue} is held by {held['owner']} / {held['session']} since {held['since']}. "
            "Ask on the issue or on the Coordination issue; --force only once its holder agreed or left it stale."
        )
    clash = conflicts(files, [(n, c) for n, c, _ in gh.claims()], skip=args.issue)
    for number, other, hit in clash:
        print(f"#{number} held by {other['owner']} / {other['session']} already touches: {', '.join(hit)}")
    if clash and not args.force:
        sys.exit("Not claimed: agree on the order with them first (--force to claim anyway).")
    gh.call("PATCH", f"/issues/{args.issue}", {"body": render(claim, args.issue) + strip(issue.get("body"))})
    gh.call("POST", f"/issues/{args.issue}/labels", {"labels": [LABEL, f"claude-{args.owner}"]})
    if not (held and same_holder(held, claim)):
        note = f"\n\n{args.note}" if args.note else ""
        gh.call(
            "POST",
            f"/issues/{args.issue}/comments",
            {"body": f"Claimed by {args.owner}'s assistant, session `{args.session}`.{note}"},
        )
    print(f"#{args.issue} claimed by {args.owner} / {args.session}.")


def cmd_release(gh, args):
    issue = gh.call("GET", f"/issues/{args.issue}")
    held = parse(issue.get("body"))
    labels = {label["name"] for label in issue["labels"]}
    if not held and LABEL not in labels:
        print(f"#{args.issue} was not claimed.")
        return
    gh.call("PATCH", f"/issues/{args.issue}", {"body": strip(issue.get("body"))})
    for name in sorted(labels & ({LABEL} | ({f"claude-{held['owner']}"} if held else set()))):
        gh.call("DELETE", f"/issues/{args.issue}/labels/{name}")
    who = f" by {held['owner']} / {held['session']}" if held else ""
    note = f"\n\n{args.note}" if args.note else ""
    gh.call("POST", f"/issues/{args.issue}/comments", {"body": f"Released{who}.{note}"})
    print(f"#{args.issue} released.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--token-file", help="a file holding the GitHub token (else GH_TOKEN or GITHUB_TOKEN)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="every claimed issue, its holder and its files")
    c = sub.add_parser("check", help="who holds these files or directories (exit 1 when someone does)")
    c.add_argument("paths", nargs="+")
    t = sub.add_parser("take", help="claim an issue")
    t.add_argument("issue", type=int)
    t.add_argument("--owner", required=True, choices=OWNERS, help="whose assistant you are")
    t.add_argument("--session", required=True, help="a short name for this session, e.g. terraform")
    t.add_argument("--files", help="comma-separated files or directories the work will change")
    t.add_argument("--note", help="a line added to the comment, e.g. the plan")
    t.add_argument("--force", action="store_true", help="take over a claim or a file another claim holds")
    r = sub.add_parser("release", help="release an issue (its PR merged, or handed over)")
    r.add_argument("issue", type=int)
    r.add_argument("--note", help="a line added to the comment, e.g. where the work stands")
    args = p.parse_args(argv)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if args.token_file:
        with open(args.token_file) as f:
            token = f.read().strip()
    if not token and args.cmd in {"take", "release"}:
        sys.exit("A token is needed to claim: set GH_TOKEN or pass --token-file.")
    handler = {"list": cmd_list, "check": cmd_check, "take": cmd_take, "release": cmd_release}[args.cmd]
    return handler(GitHub(token), args) or 0


if __name__ == "__main__":
    sys.exit(main())
