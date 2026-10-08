"""Every task type has its label in the dashboard's languages: a type without one showed as a raw identifier
(node_reboot) in the task list."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Types made from a value rather than written out (host_system.power: node_<action>).
BUILT = {"node_reboot", "node_poweroff"}


def _task_types():
    found = set()
    for path in (ROOT / "app").rglob("*.py"):
        found |= set(re.findall(r'create_task\(\s*"([a-z_0-9]+)"', path.read_text()))
    return found | BUILT


def test_every_task_type_has_a_label_in_french_and_english():
    types = _task_types()
    assert "start_vm" in types and "hyperlite_update" in types
    for lang in ("fr", "en"):
        catalog = (ROOT / "dashboard" / "src" / "next" / "i18n" / f"{lang}.js").read_text()
        labelled = set(re.findall(r'"task\.type\.([a-z_0-9]+)"', catalog))
        assert types <= labelled, f"{lang}: no label for {sorted(types - labelled)}"
