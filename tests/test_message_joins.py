"""Python joins adjacent string literals as written: a message split over two lines needs a space at the joint, or
two words are glued together ("storage pool(NFS)")."""

import ast
import io
import itertools
import pathlib
import re
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent
# A word (or a colon) on both sides of the joint: prose. Markup (`>` then `<`) and paths are left alone.
GLUED = re.compile(r"[\w:]\Z"), re.compile(r"\A[\w(]")


def _glued_joints(source):
    tokens = [
        t
        for t in tokenize.generate_tokens(io.StringIO(source).readline)
        if t.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT)
    ]
    for a, b in itertools.pairwise(tokens):
        if a.type != tokenize.STRING or b.type != tokenize.STRING:
            continue
        try:
            left, right = ast.literal_eval(a.string), ast.literal_eval(b.string)
        except ValueError:  # an f-string: its literal edges are enough
            left, right = a.string.rstrip("\"'"), b.string.lstrip("fFrRbB\"'")
        if isinstance(left, str) and isinstance(right, str) and GLUED[0].search(left) and GLUED[1].search(right):
            yield a.start[0], left[-30:] + right[:30]


def test_the_glued_joint_is_found():
    assert list(_glued_joints('x = ("a shared storage pool"\n     "(NFS)")\n'))
    assert not list(_glued_joints('x = ("a shared storage pool "\n     "(NFS)")\n'))
    assert not list(_glued_joints("x = ('<cpu>'\n     '<model/>')\n"))


def test_no_message_glues_two_words():
    found = [
        f"{path.relative_to(ROOT)}:{line}: {text!r}"
        for path in sorted((ROOT / "app").rglob("*.py"))
        for line, text in _glued_joints(path.read_text(encoding="utf-8"))
    ]
    assert not found, "\n".join(found)
