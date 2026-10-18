"""Apply a set of API v1 renames to the code (docs/design/api-v1.md): only exact names, never prose.

    python3 docs/design/api-v1/rewrite.py values en_cours termine ...   (each name with its names.json translation)

Replaced:
- string literals that are exactly the name: "en_cours", 'en_cours' (SQL inside Python too), `en_cours`;
- in the dashboard, object keys `en_cours:` and the last part of translation keys ("k8s.state.en_cours"), both in the
  code and in the translation files, since the dashboard builds those keys from the values;
- nothing else: comments and messages are reviewed by hand.

Left alone: app/core/schema_v1.py and its tests (they hold the old names on purpose) and docs/design.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NAMES = json.loads((Path(__file__).resolve().parent / "names.json").read_text())
EXTRA = {
    "etiquette": "tag",
    "creation": "creating",
    "suppression": "deleting",
}  # the glossary of 2026-10-18 lacked them
SKIP = {"app/core/schema_v1.py", "tests/test_schema_v1.py"}  # they hold the old names on purpose
GLOBS = ["app", "tests", "dashboard/src", "dashboard/e2e", "cli", "docs/api.md", "lab"]


def files():
    out = subprocess.run(["git", "ls-files", *GLOBS], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    for name in out.split():
        if name in SKIP or name.startswith("docs/design"):
            continue
        if name.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".md", ".sh")):
            yield ROOT / name


def rewrite(words):
    renames = {w: EXTRA.get(w) or NAMES[w] for w in words}
    alternatives = "|".join(sorted(map(re.escape, renames), key=len, reverse=True))
    literal = re.compile(rf"""(["'`])({alternatives})\1""")
    js_key = re.compile(rf"(?<![\w.$])({alternatives})(\s*:)(?!:)")
    i18n_key = re.compile(rf"""(["'`][\w-]+(?:\.[\w-]+)*\.)({alternatives})(["'`])""")
    changed = {}
    for path in files():
        text = path.read_text()
        new = literal.sub(lambda m: m.group(1) + renames[m.group(2)] + m.group(1), text)
        new = i18n_key.sub(lambda m: m.group(1) + renames[m.group(2)] + m.group(3), new)
        if path.suffix in (".js", ".jsx", ".ts", ".tsx"):
            new = js_key.sub(lambda m: renames[m.group(1)] + m.group(2), new)
        if new != text:
            path.write_text(new)
            changed[str(path.relative_to(ROOT))] = sum(
                a != b for a, b in zip(text.splitlines(), new.splitlines(), strict=False)
            )
    return changed


if __name__ == "__main__":
    kind, *words = sys.argv[1:]
    for name, lines in sorted(rewrite(words).items()):
        print(f"{lines:4d}  {name}")
