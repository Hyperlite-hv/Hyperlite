"""docs/proxmox-parity.json, which docs/proxmox-parity.html shows: every row well formed, every gap tied to an issue."""

import json
from pathlib import Path

DATA = json.loads((Path(__file__).resolve().parents[1] / "docs" / "proxmox-parity.json").read_text())
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
