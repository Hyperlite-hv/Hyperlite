"""The end of each log of a failed lab run, as GitHub annotations: python3 lab/annotate.py DIR.

The logs are kept as an artifact as well; an annotation is what the checks' API returns to a reader without access to
the job's logs or its artifacts.

python3 lab/annotate.py --summary DIR: after a green run, which tests passed and which were skipped, and why (no nested
virtualization on the runner skips the VMs' own tests). A green job alone does not say what ran."""

import sys
from pathlib import Path

LINES = 40
LIMIT = 4000  # characters an annotation shows


def escape(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


FAILURES = 6  # GitHub keeps ten error annotations per step: the rest for the tracebacks and the nodes' tails


def _failures(lines):
    """[(test, its "E" lines)] from pytest's failure sections (`____ test_name ____`), in order."""
    found, current = [], None
    for line in lines:
        if line.startswith("_____") and line.rstrip("_ ").strip("_ "):
            current = (line.strip("_ "), [])
            found.append(current)
        elif current is not None and line.startswith("E "):
            current[1].append(line)
    return [(test, why) for test, why in found if why]


def summary(directory):
    """pytest's short summary (`-rA`: one line per test, a skip with its reason) and its count, as one notice."""
    log = Path(directory) / "pytest.log"
    if not log.is_file():
        print("::warning title=pytest summary::no pytest.log in the lab's directory")
        return
    lines = log.read_text(errors="replace").splitlines()
    start = next((i for i, line in enumerate(lines) if "short test summary info" in line), None)
    picked = [
        line
        for line in (lines[start + 1 :] if start is not None else [])
        if line.startswith(("PASSED", "SKIPPED", "XFAIL", "XPASS", "FAILED", "ERROR"))
    ]
    count = next((line.strip("= ") for line in reversed(lines) if " in " in line and line.startswith("=")), None)
    count = count or next((line for line in reversed(lines) if line.strip()), "no result line")
    text = "\n".join([count, *picked])[:LIMIT]
    print(f"::notice title=pytest summary::{escape(text)}")


PERMISSION = (
    "Permission denied",
    "EACCES",
    "PermissionError",
    "Operation not permitted",
    "readonly database",
    "attempt to write a readonly",
    "needs the proof",
    "forbidden",
    "unable to open database",
    "not authorized",
)


def permissions(directory):
    """The probe of 4e (the API as www-data): every distinct line of the nodes' journals that says a permission was
    refused, its time and process stripped, as notices."""
    import re

    seen = {}
    for log in sorted(Path(directory).glob("n*/hyperlite.log")):
        for line in log.read_text(errors="replace").splitlines():
            if any(p in line for p in PERMISSION):
                key = re.sub(r"^\w+ +\d+ [\d:]+ \S+ ", "", line)
                key = re.sub(r"\[\d+\]", "", key)
                key = re.sub(r"pid \d+", "pid N", key)
                seen.setdefault(key, f"{log.parent.name}: {line[:600]}")
    text = "\n".join(seen.values()) or "no permission refused in the nodes' journals"
    for i in range(0, min(len(text), LIMIT * 8), LIMIT):
        print(f"::notice title=www-data probe {i // LIMIT + 1}::{escape(text[i : i + LIMIT])}")


def main(directory):
    root = Path(directory)
    logs = [root / "pytest.log"]
    for name in ("provision.log", "hyperlite.log", "cfs.log"):
        logs += sorted(root.glob(f"n*/{name}"))
    for log in logs:
        if not log.is_file():
            continue
        lines = log.read_text(errors="replace").splitlines()
        title = escape(str(log.relative_to(root))).replace(",", "%2C").replace(":", "%3A")
        if log.name == "pytest.log":
            # Which tests failed and why, whatever the length of their tracebacks: each failed test its own
            # annotation, so a long one (a Terraform plan) never hides the next one's reason.
            for test, why in _failures(lines)[:FAILURES]:
                print(f"::error title=pytest failure {escape(test)}::{escape(chr(10).join(why)[:LIMIT])}")
        # A traceback earlier in the log: its start says where, the tail only what followed.
        first = next((i for i, line in enumerate(lines) if "Traceback" in line), None)
        if first is not None and first < len(lines) - LINES:
            start = "\n".join(lines[first : first + LINES])[:LIMIT]
            print(f"::error title={title} (first traceback)::{escape(start)}")
        tail = "\n".join(lines[-LINES:])[-LIMIT:]
        # GitHub keeps ten annotations of each level per step: errors for pytest and the tracebacks, warnings for
        # the nodes' tails.
        level = "error" if log.name == "pytest.log" else "warning"
        print(f"::{level} title={title}::{escape(tail)}")


if __name__ == "__main__" and sys.argv[1:2] == ["--permissions"]:
    permissions(sys.argv[2])
    sys.exit(0)
if __name__ == "__main__":
    if sys.argv[1] == "--summary":
        summary(sys.argv[2])
    else:
        main(sys.argv[1])
