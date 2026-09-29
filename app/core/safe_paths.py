"""Path helpers that keep user-influenced names inside a base directory."""

import os
from pathlib import Path


def safe_child(base, name) -> Path:
    """Return ``base/name`` after normalisation, refusing anything that escapes ``base``
    (``..`` segments, absolute names, or a symbolic link inside ``base`` pointing out of it).
    Names are validated by the API layer as well; this is the last line of defence for code
    that builds paths from them.

    The textual check alone let a symbolic link placed in ``base`` lead anywhere; the resolved
    path is checked too (the part of it that exists: a file about to be created has none)."""
    base_s = os.path.normpath(os.fspath(base))
    full = os.path.normpath(os.path.join(base_s, os.fspath(name)))
    if not full.startswith(base_s + os.sep):
        raise ValueError(f"Path escapes its directory: {name!r}")
    real_base = os.path.realpath(base_s)
    real_full = os.path.realpath(full)
    if real_full != real_base and not real_full.startswith(real_base + os.sep):
        raise ValueError(f"Path escapes its directory through a symbolic link: {name!r}")
    return Path(full)
