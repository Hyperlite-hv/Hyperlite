"""Version of the code this process runs.

The VERSION file ships with the code, but the file on disk can change under a running process: an update installs
the new package first and restarts the service afterwards. So the version this process really runs is the one
read when it started (STARTUP_VERSION), not whatever the file says now.
"""

from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"


def read_version_file():
    try:
        return VERSION_FILE.read_text().strip() or None
    except OSError:
        return None


STARTUP_VERSION = read_version_file()
