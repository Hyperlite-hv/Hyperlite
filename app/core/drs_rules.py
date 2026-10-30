"""Load balancing's settings and rules (docs/design/drs.md, 3.4 and 3.5, #334 step 2).

A rule names VMs, and nodes for the last two kinds:

- `together`: its VMs run on one node;
- `apart`: no two of its VMs run on one node;
- `on_nodes`: its VMs run on the nodes named;
- `not_on_nodes`: its VMs never run on the nodes named.

A "must" rule binds DRS and HA (a VM with no node left that keeps it is not restarted: it goes to manual), and an
administrator's start or migration that breaks it is refused. A "should" rule is DRS's preference, and only said when
broken. `allowed()` answers whether a VM may run on a node, given where the others run (`placement()`).
"""

import re
from datetime import UTC, datetime

KINDS = ("together", "apart", "on_nodes", "not_on_nodes")
STRENGTHS = ("must", "should")
MODES = ("off", "manual", "automatic")
DEFAULTS = {"mode": "automatic", "threshold": 3}  # docs/design/drs.md, section 8
BEHAVIOURS = ("default", "manual", "excluded")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}")
VM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}")
NODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}")
MAX_MEMBERS = 64


def _store():
    from app.repositories.sqlite.drs import SqliteDrsStore

    return SqliteDrsStore()


# ---- Settings ----


def settings():
    """{"mode", "threshold"}: what was saved, else the defaults."""
    saved = _store().settings()
    mode = saved.get("mode") if saved.get("mode") in MODES else DEFAULTS["mode"]
    try:
        threshold = int(saved.get("threshold", DEFAULTS["threshold"]))
    except ValueError:
        threshold = DEFAULTS["threshold"]
    return {"mode": mode, "threshold": threshold if 1 <= threshold <= 5 else DEFAULTS["threshold"]}


def save_settings(mode, threshold):
    if mode not in MODES:
        raise ValueError(f"The mode is one of: {', '.join(MODES)}")
    if not isinstance(threshold, int) or not 1 <= threshold <= 5:
        raise ValueError("The threshold is a level from 1 (conservative) to 5 (aggressive)")
    _store().save_settings({"mode": mode, "threshold": threshold})
    return settings()


def behaviours():
    """{vm: "manual" | "excluded"} of the VMs that are not the cluster's default."""
    return _store().behaviours()


def set_behaviour(vm, behaviour):
    if not VM_RE.fullmatch(vm or ""):
        raise ValueError(f"Not a VM name: {vm!r}")
    if behaviour not in BEHAVIOURS:
        raise ValueError(f"The behaviour is one of: {', '.join(BEHAVIOURS)}")
    _store().set_behaviour(vm, behaviour)


# ---- Rules ----


def _names(values, pattern, what):
    if not isinstance(values, list) or len(values) > MAX_MEMBERS:
        raise ValueError(f"The {what} are a list of at most {MAX_MEMBERS} names")
    bad = [v for v in values if not isinstance(v, str) or not pattern.fullmatch(v)]
    if bad:
        raise ValueError(f"Not a {what[:-1]} name: {', '.join(map(str, bad))}")
    return list(dict.fromkeys(values))


def validate(rule):
    """The rule, checked and normalised; ValueError with the reason otherwise."""
    name = rule.get("name") or ""
    if not NAME_RE.fullmatch(name):
        raise ValueError("A rule's name: letters, digits, '.', '_' and '-', at most 63")
    kind, strength = rule.get("kind"), rule.get("strength", "should")
    if kind not in KINDS:
        raise ValueError(f"The kind is one of: {', '.join(KINDS)}")
    if strength not in STRENGTHS:
        raise ValueError("The strength is must or should")
    vms = _names(rule.get("vms") or [], VM_RE, "VMs")
    nodes = _names(rule.get("nodes") or [], NODE_RE, "nodes")
    if kind in ("together", "apart") and len(vms) < 2:
        raise ValueError("A rule that keeps VMs together or apart names at least two VMs")
    if kind in ("on_nodes", "not_on_nodes"):
        if not vms or not nodes:
            raise ValueError("A rule on nodes names at least one VM and one node")
    else:
        nodes = []
    return {
        "name": name,
        "kind": kind,
        "strength": strength,
        "vms": vms,
        "nodes": nodes,
        "enabled": bool(rule.get("enabled", True)),
    }


def rules():
    return _store().rules()


def save_rule(rule, username):
    clean = validate(rule)
    _store().save_rule(clean, username, datetime.now(UTC).isoformat())
    return _store().rule(clean["name"])


def delete_rule(name):
    return _store().delete_rule(name)


# ---- Where the VMs run, and what a rule allows ----


def placement():
    """{vm: node} of the running VMs of the cluster, read from the members' statuses (app/core/statd.py)."""
    from app.core import statd

    out = {}
    for node, status in statd.table().items():
        for vm in status.get("vms") or []:
            if vm.get("state") in ("active", "running") and vm.get("name"):
                out[vm["name"]] = node
    return out


def _breaks(rule, vm, node, where):
    """Why putting `vm` on `node` breaks `rule`, given where the other VMs run (`where`), or None."""
    if vm not in rule["vms"]:
        return None
    others = {v: n for v, n in where.items() if v in rule["vms"] and v != vm}
    kind = rule["kind"]
    if kind == "together":
        elsewhere = sorted({n for n in others.values() if n != node})
        return f"its VMs run together, on {', '.join(elsewhere)}" if elsewhere else None
    if kind == "apart":
        here = sorted(v for v, n in others.items() if n == node)
        return f"its VMs run apart, and {', '.join(here)} runs on {node}" if here else None
    if kind == "on_nodes" and node not in rule["nodes"]:
        return f"its VMs run on {', '.join(rule['nodes'])} only"
    if kind == "not_on_nodes" and node in rule["nodes"]:
        return f"its VMs never run on {node}"
    return None


def broken(vm, node, where=None, strengths=STRENGTHS):
    """[(rule, why)] of the enabled rules of `strengths` that `vm` on `node` would break."""
    where = placement() if where is None else where
    out = []
    for rule in rules():
        if rule["enabled"] and rule["strength"] in strengths:
            why = _breaks(rule, vm, node, where)
            if why:
                out.append((rule, why))
    return out


def allowed(vm, node, where=None):
    """True when `vm` may run on `node`: no "must" rule broken."""
    return not broken(vm, node, where, strengths=("must",))


def refuse_if_broken(vm, node, where=None):
    """Raises RuntimeError naming a "must" rule that `vm` on `node` would break; returns the "should" rules it would
    (the caller says them), as [(rule name, why)]."""
    found = broken(vm, node, where)
    must = [(r["name"], why) for r, why in found if r["strength"] == "must"]
    if must:
        name, why = must[0]
        raise RuntimeError(f"Rule '{name}' (must): {why}")
    return [(r["name"], why) for r, why in found]
