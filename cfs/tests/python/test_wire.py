"""The client's wire values are the daemon's: read from cfs/src/cfs.h, so a status added on one side only fails here
instead of surfacing as a generic error on a cluster. Needs no daemon."""

import re
from pathlib import Path

from app.core import cfs_client

HEADER = Path(__file__).resolve().parents[2] / "src" / "cfs.h"


def _enum(name):
    body = re.search(r"enum " + name + r" \{(.*?)\};", HEADER.read_text(), re.S).group(1)
    return {key: int(value) for key, value in re.findall(r"CFS_(\w+) = (\d+)", body)}


def test_every_status_of_the_daemon_has_the_same_value_in_the_client():
    statuses = _enum("cfs_status")
    assert statuses, "no status found in cfs.h"
    for key, value in statuses.items():
        assert getattr(cfs_client, key) == value, key


def test_every_refusal_status_has_its_own_exception():
    for key, value in _enum("cfs_status").items():
        if key not in ("OK", "INVALID", "TOO_LARGE", "FORBIDDEN", "INTERNAL"):
            assert value in cfs_client._ERRORS, key
