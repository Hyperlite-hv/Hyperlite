"""Phase 1 of the control plane migration (docs/design/control-plane-v2-migration.md): routers stop reaching SQLite
themselves. This list only shrinks: a router that no longer needs get_conn must leave it, a new one never enters."""

import pathlib
import re

# Routers that still run SQL directly, until their domain moves behind a repository.
STILL_ALLOWED = {
    "auth.py",
    "sso.py",
}

ROUTERS = pathlib.Path(__file__).resolve().parent.parent / "app" / "routers"


def _using_get_conn():
    return {
        str(path.relative_to(ROUTERS)) for path in ROUTERS.rglob("*.py") if re.search(r"\bget_conn\b", path.read_text())
    }


def test_no_new_router_reaches_sqlite():
    assert _using_get_conn() - STILL_ALLOWED == set()


def test_the_allowed_list_shrinks_with_each_lot():
    assert STILL_ALLOWED - _using_get_conn() == set(), "remove the routers that no longer use get_conn from the list"
