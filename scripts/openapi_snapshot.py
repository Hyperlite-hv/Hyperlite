"""Write the OpenAPI description of API v1 to docs/api/openapi-v1.json, the contract tests/test_openapi_contract.py
holds the code to: inside v1 a change may only add (docs/design/api-v1.md, "Versioning rule after 1.0").

    python3 scripts/openapi_snapshot.py      after an addition to the API, to record it
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs" / "api" / "openapi-v1.json"


def current():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("HYPERLITE_DB_PATH", "/nonexistent/hyperlite.db")  # importing the app opens nothing
    # ...and writes nowhere of the machine's: the libraries it creates at import (app/core/paths.py) go to a temporary place.
    os.environ.setdefault("HYPERLITE_VAR_DIR", tempfile.mkdtemp(prefix="hyperlite-openapi-"))
    os.environ.setdefault("HYPERLITE_ETC_DIR", tempfile.mkdtemp(prefix="hyperlite-openapi-"))
    from app.main import app

    spec = app.openapi()
    spec.pop("info", None)  # the version of the package, which changes with every release
    return spec


if __name__ == "__main__":
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(current(), indent=1, sort_keys=True) + "\n")
    print(f"{SNAPSHOT.relative_to(ROOT)}: {len(current()['paths'])} paths")
