"""Inventory of the French names of the API and the database (docs/design/api-v1.md, step 1).

Run from the repository root: python3 docs/design/api-v1/extract.py > docs/design/api-v1/inventory.json
It lists every identifier that has a French word in it, with where it is seen:
- `column`: a database column (read from a fresh database made by init_db, so nothing is guessed);
- `table`: a database table;
- `string`: a string constant of the code (a JSON key, a stored or returned value, a query parameter);
- `field`: a field of a request model or a parameter of a route;
- `path`: a word of a route's path.
A word is French when the French word list has it and the English one does not (accents ignored).
"""

import ast
import json
import os
import re
import sqlite3
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


def _plain(word):
    return "".join(c for c in unicodedata.normalize("NFD", word.lower()) if unicodedata.category(c) != "Mn")


def _words(path):
    return {_plain(w.strip()) for w in Path(path).read_text(errors="ignore").splitlines() if w.strip().isalpha()}


ENGLISH = _words("/usr/share/dict/american-english")
FRENCH = _words("/usr/share/dict/french") - ENGLISH
# Short words both lists miss or confuse, decided by hand.
FRENCH |= {"le", "la", "les", "par", "de", "du", "des", "go", "mo", "maj", "noeud", "noeuds", "etat", "cle", "hote"}
FRENCH -= {"id", "ip", "ok", "os", "ssh", "tls", "url", "vm", "dn", "rp", "sso", "ca", "bps", "pct", "mb", "gb", "s"}


def french(name):
    return any(part in FRENCH for part in name.split("_") if part)


def columns():
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("HYPERLITE_SECRET_KEY", "x" * 64)
    from cryptography.fernet import Fernet

    os.environ.setdefault("HYPERLITE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from app.core import database

    with tempfile.TemporaryDirectory() as tmp:
        database.DB_PATH = Path(tmp) / "inventory.db"
        database.init_db()
        db = sqlite3.connect(database.DB_PATH)
        tables = [
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        ]
        return {t: [r[1] for r in db.execute(f"PRAGMA table_info({t})")] for t in tables}


def code_names():
    strings, fields = defaultdict(set), defaultdict(set)
    for path in sorted((ROOT / "app").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and IDENT.match(node.value):
                strings[node.value].add(rel)
            elif isinstance(node, ast.ClassDef) and any(getattr(b, "id", "") == "BaseModel" for b in node.bases):
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        fields[item.target.id].add(rel)
            elif (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and rel.startswith("app/routers")
                and any("router." in ast.unparse(d) for d in node.decorator_list)
            ):
                for arg in node.args.args:
                    fields[arg.arg].add(rel)
    return strings, fields


def path_words():
    words = defaultdict(set)
    for path in sorted((ROOT / "app" / "routers").rglob("*.py")):
        for m in re.finditer(r'@router\.\w+\("([^"]*)"', path.read_text()):
            for word in re.split(r"[/\-.]", m.group(1)):
                if word and not word.startswith("{"):
                    words[word].add(str(path.relative_to(ROOT)))
    return words


def main():
    found = defaultdict(lambda: {"kinds": set(), "where": set()})

    def note(name, kind, where):
        if IDENT.match(name.replace("-", "_")) and french(name.replace("-", "_")):
            found[name]["kinds"].add(kind)
            found[name]["where"].update(where)

    for table, cols in columns().items():
        note(table, "table", {f"table {table}"})
        for col in cols:
            note(col, "column", {f"table {table}"})
    strings, fields = code_names()
    for name, where in strings.items():
        note(name, "string", where)
    for name, where in fields.items():
        note(name, "field", where)
    for word, where in path_words().items():
        note(word, "path", where)
    out = {name: {"kinds": sorted(v["kinds"]), "where": sorted(v["where"])[:6], "seen": len(v["where"])}
           for name, v in sorted(found.items())}  # fmt: skip
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
