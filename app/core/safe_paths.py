"""Path helpers that keep user-influenced names inside a base directory."""

import os
from pathlib import Path


def safe_child(base, name) -> Path:
    """Return ``base/name`` after normalisation, refusing anything that escapes ``base``
    (``..`` segments, absolute names). Names are validated by the API layer as well; this is
    the last line of defence for code that builds paths from them."""
    base_s = os.path.normpath(os.fspath(base))
    full = os.path.normpath(os.path.join(base_s, os.fspath(name)))
    if not full.startswith(base_s + os.sep):
        raise ValueError(f"Path escapes its directory: {name!r}")
    return Path(full)
