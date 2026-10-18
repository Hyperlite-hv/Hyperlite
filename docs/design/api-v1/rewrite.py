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
SKIP = {
    "app/core/schema_v1.py",  # it holds the old names on purpose, and so do its tests
    "tests/test_schema_v1.py",
    "app/core/password_policy.py",  # French words people use as passwords, refused as such
    "app/routers/api_docs.py",  # its page has a French paragraph; renamed once by hand
    "tests/test_cluster_compat.py",  # it imitates libvirt's French messages; renamed once by hand
}
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


# ---- Names (keys, columns, fields) -------------------------------------------------------------------------------
#
#   python3 docs/design/api-v1/rewrite.py names        every name of names.json but the ones listed in KEEP
#
# Python: tokens, so prose is never touched by accident: a name token (a key, a column, a parameter, an attribute, a
# variable) and a whole word inside a string (a key, SQL) or a comment. A scope where the new name is already used for
# something else is not changed: it is reported, to be done by hand.
# JavaScript: whole words, never one joined by a hyphen (a CSS class). Translation files: keys and {placeholders}
# only, the text stays in its language. Go: JSON tags.

import ast  # noqa: E402
import io  # noqa: E402
import tokenize  # noqa: E402

KEEP = {
    "le": "too short to rename blindly: by hand",
    "activer": "a route word: step 3",
    "creer": "a route word: step 3",
    "desactiver": "a route word: step 3",
    "rejoindre": "a route word: step 3",
    "retirer": "a route word: step 3",
    "installer": "also the installer/ directory: the few French uses by hand",
    "resume": "also English (libvirt's dom.resume()): the key renamed by hand",
    "reception": "also English: the key renamed by hand",
    "retire": "also English: the key renamed by hand",
    "JetonAPI": "the OpenAPI security scheme, renamed by hand",
    "hl": "not French",
    "vlan": "not French",
}
WORD = r"(?<![\w$-]){}(?![\w$-])"


def _all_names():
    return {w: n for w, n in NAMES.items() if w not in KEEP and w != n}


def _scopes(tree):
    yield tree
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield node


def _names_in(scope):
    """The names a scope itself uses, not those of the functions and classes defined inside it (their own scopes)."""
    out = set()
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Name):
            out.add(node.id)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                todo.extend(d for d in node.args.defaults + node.args.kw_defaults if d is not None)  # enclosing scope
            continue
        todo.extend(ast.iter_child_nodes(node))
    return out


def _conflicts(text, renames):
    """{old} whose new name already names something in a scope where the old one is used."""
    tree = ast.parse(text)
    out = set()
    for scope in _scopes(tree):
        used = _names_in(scope)
        out |= {old for old, new in renames.items() if old in used and new in used}
    return out


def _python(text, renames, path, report):
    conflicts = _conflicts(text, renames)
    if conflicts:
        report.append(f"{path}: variables {sorted(conflicts)} kept: their new name is taken in the same scope")
    # In such a file the old name keeps naming its variables; its keys, columns, attributes and comments are renamed.
    variables = {o: n for o, n in renames.items() if o not in conflicts}
    word = re.compile(WORD.format("(" + "|".join(sorted(map(re.escape, renames), key=len, reverse=True)) + ")"))
    lines = text.splitlines(keepends=True)
    edits = []
    previous = None
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.NAME and tok.string in renames:
            attribute = previous is not None and previous.type == tokenize.OP and previous.string == "."
            if attribute or tok.string in variables:
                edits.append((tok.start, tok.end, renames[tok.string]))
        elif tok.type in (tokenize.STRING, tokenize.COMMENT, getattr(tokenize, "FSTRING_MIDDLE", -1)):
            new = word.sub(lambda m: renames[m.group(1)], tok.string)
            if new != tok.string:
                edits.append((tok.start, tok.end, new))
        if tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT):
            previous = tok
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    out = text
    for (sr, sc), (er, ec), new in sorted(edits, reverse=True):
        a, b = offsets[sr - 1] + sc, offsets[er - 1] + ec
        out = out[:a] + new + out[b:]
    return out


def _javascript(text, renames, path):
    word = re.compile(WORD.format("(" + "|".join(sorted(map(re.escape, renames), key=len, reverse=True)) + ")"))
    if "/i18n/" not in path:
        return word.sub(lambda m: renames[m.group(1)], text)
    # Every "key": of the catalogue (several may share a line), and the {placeholders} of the texts.
    text = re.sub(
        r'"([\w.-]+)"(\s*:)',
        lambda m: '"' + ".".join(renames.get(part, part) for part in m.group(1).split(".")) + '"' + m.group(2),
        text,
    )
    return re.sub(
        r"\{\{?\s*(\w+)\s*\}?\}",
        lambda p: p.group(0).replace(p.group(1), renames.get(p.group(1), p.group(1))),
        text,
    )


def _javascript_tokens(paths, renames):
    """JavaScript and TypeScript by tokens (rewrite_js.cjs, with the dashboard's own parser)."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(renames, f)
    done = subprocess.run(
        ["node", str(Path(__file__).with_name("rewrite_js.cjs")), f.name, *map(str, paths)],
        cwd=ROOT / "dashboard",
        capture_output=True,
        text=True,
        check=True,
    )
    Path(f.name).unlink()
    return done.stdout, done.stderr


def rename_names():
    renames = _all_names()
    report, changed = [], {}
    scripts = [p for p in files() if p.suffix in (".js", ".jsx", ".ts", ".tsx") and "/i18n/" not in str(p)]
    out, err = _javascript_tokens(scripts, renames)
    report += err.splitlines()
    for line in out.splitlines():
        count, name = line.split("\t")
        changed[str(Path(name).resolve().relative_to(ROOT))] = int(count)
    for path in files():
        if path in scripts:
            continue
        rel = str(path.relative_to(ROOT))
        text = path.read_text()
        if path.suffix == ".py":
            new = _python(text, renames, rel, report)
        elif path.suffix in (".js", ".jsx", ".ts", ".tsx"):
            new = _javascript(text, renames, rel)
        elif path.suffix == ".go":
            new = re.sub(r'json:"(\w+)', lambda m: 'json:"' + renames.get(m.group(1), m.group(1)), text)
        elif path.suffix == ".md":
            new = re.sub(r"`(\w+)`", lambda m: "`" + renames.get(m.group(1), m.group(1)) + "`", text)
        else:
            continue
        if new != text:
            path.write_text(new)
            changed[rel] = sum(a != b for a, b in zip(text.splitlines(), new.splitlines(), strict=False))
    return changed, report


if __name__ == "__main__":
    kind, *words = sys.argv[1:]
    if kind == "names":
        changed, report = rename_names()
        print("\n".join(report))
    else:
        changed = rewrite(words)
    for name, lines in sorted(changed.items()):
        print(f"{lines:4d}  {name}")
