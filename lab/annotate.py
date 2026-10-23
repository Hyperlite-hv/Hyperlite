"""The end of each log of a failed lab run, as GitHub annotations: python3 lab/annotate.py DIR.

The logs are kept as an artifact as well; an annotation is what the checks' API returns to a reader without access to
the job's logs or its artifacts."""

import sys
from pathlib import Path

LINES = 40
LIMIT = 4000  # characters an annotation shows


def escape(text):
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(directory):
    root = Path(directory)
    logs = [root / "pytest.log", *sorted(root.glob("n*/provision.log")), *sorted(root.glob("n*/hyperlite.log"))]
    for log in logs:
        if not log.is_file():
            continue
        lines = log.read_text(errors="replace").splitlines()
        title = escape(str(log.relative_to(root))).replace(",", "%2C").replace(":", "%3A")
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
    main(sys.argv[1])
