#!/usr/bin/env python3
"""The changelog's unreleased changes, one file per change: no two pull requests edit the same file.

Every pull request used to add its entry under `## [Unreleased]` in CHANGELOG.md, so two pull requests open at the
same time conflicted as soon as one was merged, and a release, which renames that section, conflicted with all of
them. A pull request now adds a fragment instead, `changelog.d/<issue>-<slug>.<type>.md`, holding its bullet(s), and
the release pull request turns the fragments into the version's section:

    python3 scripts/changelog.py check                  the fragments are well formed (the test suite runs it)
    python3 scripts/changelog.py unreleased             what is not released yet, as the version's section will read
    python3 scripts/changelog.py release 1.1.4          write `## [1.1.4] - <today>` into CHANGELOG.md, remove the fragments

Entries still written under `## [Unreleased]` in CHANGELOG.md, as pull requests opened before the fragments do, are
kept: they come first in their type, and `release` moves them into the version's section too. Standard library only.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Keep a Changelog's types, in its order: a section lists them so.
TYPES = ("added", "changed", "deprecated", "removed", "fixed", "security")
FRAGMENT = re.compile(r"^(?P<name>[a-z0-9][a-z0-9._-]*)\.(?P<type>[a-z]+)\.md$")
UNRELEASED = "## [Unreleased]"
# Files of the directory that are not fragments.
OTHER = {"README.md", ".gitkeep"}


def fragments(root=ROOT):
    """[(path, type, text)], in the order of their names, the issue number first (so #99 comes before #440)."""

    def order(path):
        number = re.match(r"\d+", path.name)
        return (int(number.group()) if number else sys.maxsize, path.name)

    folder = root / "changelog.d"
    out = []
    for path in sorted(folder.glob("*"), key=order) if folder.is_dir() else []:
        if path.name in OTHER:
            continue
        m = FRAGMENT.match(path.name)
        out.append((path, m.group("type") if m else None, path.read_text(encoding="utf-8").strip()))
    return out


def problems(root=ROOT):
    """What is wrong with the fragments, one sentence each: none for a changelog the release can be made from."""
    out = []
    for path, kind, text in fragments(root):
        where = f"changelog.d/{path.name}"
        if kind not in TYPES:
            out.append(f"{where}: name it <issue>-<slug>.<type>.md, type one of {', '.join(TYPES)}")
        if not text.startswith("- "):
            out.append(f"{where}: write one or more bullets, each starting with '- '")
        if re.search(r"^#", text, re.M):
            out.append(f"{where}: no heading: the release writes the version's and the type's")
    return out


def _split(changelog):
    """(head up to and including the Unreleased heading, its body, the released sections)."""
    start = changelog.find(UNRELEASED + "\n")
    if start < 0:
        raise SystemExit(f"CHANGELOG.md has no '{UNRELEASED}' heading")
    body_at = start + len(UNRELEASED) + 1
    nxt = re.search(r"^## \[", changelog[body_at:], re.M)
    end = body_at + nxt.start() if nxt else len(changelog)
    return changelog[:body_at], changelog[body_at:end], changelog[end:]


def _by_type(body):
    """The Unreleased body's entries by type, as {type or None: text}; None holds what no `### Type` heading names."""
    out = {}
    parts = re.split(r"^### (.+)\n", body, flags=re.M)
    sections = [(None, parts[0]), *zip(parts[1::2], parts[2::2], strict=True)]
    for heading, text in sections:
        kind = heading.strip().lower() if heading else None
        if kind not in TYPES and heading:  # a heading of its own: kept with its entries, before the typed ones
            kind, text = None, f"### {heading.strip()}\n\n{text.strip()}"
        if text.strip():
            out[kind] = (out.get(kind, "") + "\n\n" + text.strip()).strip()
    return out


def unreleased(root=ROOT):
    """The unreleased changes as the version's section will read them: entries left in CHANGELOG.md first, then the
    fragments, under one `### Type` heading each."""
    wrong = problems(root)
    if wrong:
        raise SystemExit("\n".join(wrong))
    _, body, _ = _split((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    entries = {kind: [text] for kind, text in _by_type(body).items()}
    for _, kind, text in fragments(root):
        entries.setdefault(kind, []).append(text)
    parts = ["\n\n".join(entries[None])] if None in entries else []
    for kind in TYPES:
        if kind in entries:
            parts.append(f"### {kind.capitalize()}\n\n" + "\n\n".join(entries[kind]))
    return "\n\n".join(parts)


def release(version, date, root=ROOT):
    """Write the version's section from the unreleased changes and remove the fragments. Returns the fragments removed."""
    path = root / "CHANGELOG.md"
    changelog = path.read_text(encoding="utf-8")
    if re.search(rf"^## \[{re.escape(version)}\]", changelog, re.M):
        raise SystemExit(f"CHANGELOG.md already has a section for {version}")
    body = unreleased(root)
    if not body:
        raise SystemExit("Nothing to release: no fragment in changelog.d/ and nothing under Unreleased")
    head, _, released = _split(changelog)
    path.write_text(f"{head}\n## [{version}] - {date}\n\n{body}\n\n{released}", encoding="utf-8")
    removed = [p for p, _, _ in fragments(root)]
    for p in removed:
        p.unlink()
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scripts/changelog.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="the fragments are well formed (exit 1 when not)")
    sub.add_parser("unreleased", help="what is not released yet, as the version's section will read")
    r = sub.add_parser("release", help="write the version's section into CHANGELOG.md and remove the fragments")
    r.add_argument("version", help="the version released, e.g. 1.1.4")
    r.add_argument("--date", default=datetime.date.today().isoformat(), help="its date (default: today)")
    for p in sub.choices.values():
        p.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)  # another checkout: for the tests
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.cmd == "check":
        wrong = problems(root)
        print("\n".join(wrong) or f"{len(fragments(root))} fragment(s), well formed.")
        return 1 if wrong else 0
    if args.cmd == "unreleased":
        text = unreleased(root)
        if text:
            print(text)
        return 0
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        print(f"{args.version} is not a version (X.Y.Z)", file=sys.stderr)
        return 2
    removed = release(args.version, args.date, root)
    print(f"CHANGELOG.md: section {args.version} written from {len(removed)} fragment(s), which were removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
