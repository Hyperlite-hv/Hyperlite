"""Phase 1 of the control plane migration (docs/design/control-plane-v2-migration.md): routers, then the core
modules, stop reaching SQLite themselves. These lists only shrink: a module that no longer needs get_conn must leave
its list, a new one never enters."""

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

STILL_ALLOWED = set()  # phase 1 done for the routers: none reaches SQLite any more

# Core modules not migrated yet (database.py itself defines get_conn and the schema).
CORE_STILL_ALLOWED = {
    "api_docs.py",
    "config_copy.py",
    "database.py",
    "ha.py",
    "ha_fencing.py",
    "ha_watch.py",
    "k8s_cluster.py",
    "network_firewall.py",
    "notifications.py",
    "shared_pools.py",
    "update_check.py",
}


def _using_get_conn(folder):
    root = APP / folder
    return {str(path.relative_to(root)) for path in root.rglob("*.py") if re.search(r"\bget_conn\b", path.read_text())}


def test_no_new_router_reaches_sqlite():
    assert _using_get_conn("routers") - STILL_ALLOWED == set()


def test_the_allowed_list_shrinks_with_each_lot():
    assert STILL_ALLOWED - _using_get_conn("routers") == set(), "remove the routers that no longer use get_conn"


def test_no_new_core_module_reaches_sqlite():
    assert _using_get_conn("core") - CORE_STILL_ALLOWED == set(), "go through a repository (app/repositories)"


def test_the_core_list_shrinks_with_each_lot():
    assert CORE_STILL_ALLOWED - _using_get_conn("core") == set(), "remove the modules that no longer use get_conn"
