"""docs/proxmox-parity.json, which docs/proxmox-parity.html shows: every row well formed, every gap tied to an issue."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "docs" / "proxmox-parity.json").read_text())
ROWS = [row for category in DATA["categories"] for row in category["rows"]]


def test_every_row_says_what_both_do_in_both_languages():
    for category in DATA["categories"]:
        assert category["fr"] and category["en"] and category["id"]
    for row in ROWS:
        assert row["fr"] and row["en"] and row["pve"] and row["hl"], row
        assert row["status"] in {"ahead", "parity", "partial", "missing"}, row


def test_every_gap_has_its_issue_so_the_page_can_follow_it():
    for row in ROWS:
        if row["status"] in {"partial", "missing"}:
            assert isinstance(row.get("issue"), int), row["en"]


def test_a_release_that_cites_a_tracked_issue_finds_the_file_reviewed_since():
    """The comparison is the project's reference (Antho, 2026-10-22: "c'est notre source de vérité, il doit être à jour
    tout le temps"); it had stayed as written while #511 shipped four steps. A released version whose notes cite the
    issue of a row, dated after the file's last review, fails here until the rows are reviewed and `checked` moved to
    that day."""
    tracked = {row["issue"] for row in ROWS if row.get("issue")}
    changelog = (ROOT / "CHANGELOG.md").read_text()
    for version, date, body in re.findall(
        r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})\n(.*?)(?=^## \[|\Z)", changelog, re.M | re.S
    ):
        if date <= DATA["checked"]:
            continue
        cited = sorted({int(n) for n in re.findall(r"#(\d+)", body)} & tracked)
        assert not cited, (
            f"{version} ({date}) cites {cited}: review their rows in docs/proxmox-parity.json and set checked to {date}"
        )
