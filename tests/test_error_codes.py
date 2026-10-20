"""The error codes of API v1 (app/core/error_codes.py): every error names one, from a fixed and documented list."""

import re
import subprocess
from pathlib import Path

from app.core import error_codes

ROOT = Path(__file__).resolve().parents[1]


def test_every_error_names_a_code(client, auth_headers):
    headers = auth_headers("ana")
    assert client.get("/groups/9999", headers=headers).json()["code"] in ("not_found", "method_not_allowed")
    assert client.get("/groups").json() == {"detail": "Not authenticated", "code": "unauthorized"}
    r = client.post("/groups", json={"name": 7}, headers=headers)  # a number where a text is expected
    assert r.status_code == 422 and r.json()["code"] == "invalid"
    client.post("/groups", json={"name": "ops"}, headers=headers)
    assert client.post("/groups", json={"name": "ops"}, headers=headers).json()["code"] in ("invalid", "conflict")


def test_every_code_the_code_uses_is_listed_and_documented():
    used = set()
    out = subprocess.run(["git", "ls-files", "app"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for name in out.split():
        if name.endswith(".py"):
            used |= set(re.findall(r'"code":\s*"([a-z_]+)"', (ROOT / name).read_text()))
    used |= set(error_codes.BY_STATUS.values())
    assert used <= set(error_codes.CODES), sorted(used - set(error_codes.CODES))
    docs = (ROOT / "docs" / "api.md").read_text()
    assert [c for c in error_codes.CODES if f"`{c}`" not in docs] == []
