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
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-LINES:])[-LIMIT:]
        title = escape(str(log.relative_to(root))).replace(",", "%2C").replace(":", "%3A")
        print(f"::error title={title}::{escape(tail)}")


if __name__ == "__main__":
    main(sys.argv[1])
