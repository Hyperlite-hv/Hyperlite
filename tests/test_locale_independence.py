"""Hyperlite runs on servers in any language (#448, F1): libvirtd writes its errors in the system's language ("Modèle
de CPU unknown" on a French server), and the service ran in the system's locale too, so the tools it reads answered in
it. The service runs in C.UTF-8, and no code reads the text of an error: error codes and exception types only."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_service_runs_in_a_fixed_locale():
    unit = (ROOT / "installer" / "hyperlite.service").read_text()
    assert "Environment=LC_ALL=C.UTF-8" in unit


def test_no_code_matches_the_text_of_an_error():
    pattern = re.compile(r"""["'][^"']+["']\s+in\s+str\((e|exc|err|error)\)""")
    found = [
        f"{path.relative_to(ROOT)}:{n}"
        for path in (ROOT / "app").rglob("*.py")
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert found == [], f"read the error's code or type instead: {found}"
