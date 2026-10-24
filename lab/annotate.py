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
            # Which tests failed and why, whatever the length of their tracebacks.
            why = [line for line in lines if line.startswith(("E ", "FAILED", "ERROR", "_____"))][:80]
            if why:
                print(f"::error title=pytest failures::{escape(chr(10).join(why)[:LIMIT])}")
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


if __name__ == "__main__":
    if sys.argv[1] == "--summary":
        summary(sys.argv[2])
    else:
        main(sys.argv[1])
