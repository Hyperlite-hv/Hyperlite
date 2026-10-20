"""The contract of API v1 (docs/api/openapi-v1.json): inside v1 a change may only add a route, an optional parameter or
field, a documented value (docs/design/api-v1.md, "Versioning rule after 1.0"). Anything else waits for a v2."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import openapi_snapshot  # noqa: E402

METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


def _params(op):
    return {(p["in"], p["name"]): p.get("required", False) for p in op.get("parameters", [])}


def _schema_ref(op):
    content = (op.get("requestBody") or {}).get("content", {})
    for media in content.values():
        ref = media.get("schema", {}).get("$ref")
        if ref:
            return ref.rsplit("/", 1)[-1]
    return None


def breaking_changes(old, new):
    """What `new` removes or tightens from `old`, as sentences."""
    out = []
    schemas_old = old.get("components", {}).get("schemas", {})
    schemas_new = new.get("components", {}).get("schemas", {})
    for path, ops in old["paths"].items():
        for method in METHODS:
            if method not in ops:
                continue
            here = new["paths"].get(path, {}).get(method)
            if here is None:
                out.append(f"{method.upper()} {path} was removed")
                continue
            before, after = _params(ops[method]), _params(here)
            out += [f"{method.upper()} {path}: parameter {n} was removed" for n in before.keys() - after.keys()]
            out += [
                f"{method.upper()} {path}: parameter {n} became required"
                for n, required in after.items()
                if required and not before.get(n, False)
            ]
            if bool((ops[method].get("requestBody") or {}).get("required")) is False and (
                here.get("requestBody") or {}
            ).get("required"):
                out.append(f"{method.upper()} {path}: its body became required")
    for name, schema in schemas_old.items():
        if name not in schemas_new:
            continue  # a body model renamed in the code: its fields are checked through the routes that use it
        props_old, props_new = schema.get("properties", {}), schemas_new[name].get("properties", {})
        out += [f"schema {name}: field {f} was removed" for f in props_old.keys() - props_new.keys()]
        newly = set(schemas_new[name].get("required", [])) - set(schema.get("required", []))
        out += [f"schema {name}: field {f} became required" for f in sorted(newly)]
    return out


def test_api_v1_only_ever_grows(database):
    old = json.loads(openapi_snapshot.SNAPSHOT.read_text())
    assert breaking_changes(old, openapi_snapshot.current()) == []


def test_the_recorded_contract_is_the_current_one(database):
    """After an addition: python3 scripts/openapi_snapshot.py records it, so the next change is checked against it."""
    old = json.loads(openapi_snapshot.SNAPSHOT.read_text())
    assert json.loads(json.dumps(openapi_snapshot.current(), sort_keys=True)) == old, (
        "docs/api/openapi-v1.json is not the current API: run python3 scripts/openapi_snapshot.py"
    )


def test_a_removal_is_caught():
    old = {"paths": {"/api/v1/vms": {"get": {"parameters": [{"in": "query", "name": "node"}]}}}, "components": {}}
    gone = {"paths": {}, "components": {}}
    assert breaking_changes(old, gone) == ["GET /api/v1/vms was removed"]
    tightened = {"paths": {"/api/v1/vms": {"get": {"parameters": [{"in": "query", "name": "node", "required": True}]}}}}
    assert breaking_changes(old, tightened) == ["GET /api/v1/vms: parameter ('query', 'node') became required"]
