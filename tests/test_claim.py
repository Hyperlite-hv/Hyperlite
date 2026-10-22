""".github/scripts/claim.py: the claim block written on an issue reads back, and the page parses the same marker."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("claim", ROOT / ".github" / "scripts" / "claim.py")
claim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claim)

HELD = {"owner": "antho", "session": "terraform", "since": "2026-10-22", "files": ["app/core/cluster_view.py", "cli/"]}


def test_a_written_claim_reads_back_and_leaves_the_description_as_it_was():
    body = "## What\n\nThe provider."
    written = claim.render(HELD, 330) + body
    assert claim.parse(written) == HELD
    assert claim.strip(written) == body
    assert claim.parse(claim.strip(written)) is None


def test_a_new_claim_replaces_the_old_one_instead_of_stacking():
    first = claim.render(HELD, 330) + "text"
    second = claim.render({**HELD, "session": "other"}, 330) + claim.strip(first)
    assert second.count(claim.MARKER) == 1
    assert claim.parse(second)["session"] == "other"


def test_a_damaged_block_is_no_claim():
    assert claim.parse("<!-- claim {not json} -->\n<!-- /claim -->") is None
    assert claim.parse('<!-- claim {"owner": "antho"} -->\n<!-- /claim -->') is None
    assert claim.parse(None) is None


def test_files_overlap_when_equal_or_one_holds_the_other():
    assert claim.overlaps("cli/", "cli/hyperlite/client.go")
    assert claim.overlaps("app/core/x.py", "app/core/x.py")
    assert not claim.overlaps("app/core", "app/core_extra.py")
    assert not claim.overlaps("app/core/x.py", "app/core/y.py")


def test_check_names_the_claims_that_hold_a_file_and_skips_the_issue_itself():
    claims = [(330, HELD), (511, {**HELD, "session": "auth", "files": ["app/auth/"]})]
    assert [n for n, _, _ in claim.conflicts(["cli/hyperlite/x.go"], claims)] == [330]
    assert claim.conflicts(["cli/hyperlite/x.go"], claims, skip=330) == []
    assert claim.conflicts(["docs/x.md"], claims) == []


def test_the_page_parses_the_marker_the_script_writes():
    page = (ROOT / "docs" / "proxmox-parity.html").read_text()
    assert r"/<!-- claim (\{.*?\}) -->[\s\S]*?<!-- \/claim -->/" in page
    assert f'labels={claim.LABEL}"' in page or f"labels={claim.LABEL}&" in page
    assert f"STALE_DAYS = {claim.STALE_DAYS}" in page


def test_every_owner_has_its_label_and_is_accepted_by_take():
    parser_owners = claim.OWNERS
    assert len(set(parser_owners)) == len(parser_owners) and all(o.isalnum() and o.islower() for o in parser_owners)
    assert "joachim" in parser_owners
