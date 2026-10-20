"""The error codes of API v1 (docs/design/api-v1.md, "Shape"; docs/api.md, "Errors"): every error answers
{"detail": <a sentence for a person>, "code": <one of CODES>}, so a script tests a code, never an English sentence.

An error that names no code of its own gets the one of its HTTP status (BY_STATUS). The list is fixed: a code is only
ever added, and tests/test_error_codes.py fails on a code the code base uses that is not listed here.
"""

CODES = {
    # By HTTP status, for an error that names nothing more precise.
    "invalid": "The request is not valid: a field is missing or wrong (400, 422).",
    "unauthorized": "No session, or one that expired or was revoked (401).",
    "forbidden": "The account may not do this (403).",
    "not_found": "Nothing by that name or id (404).",
    "method_not_allowed": "The route exists, not with this method (405).",
    "conflict": "The request conflicts with the current state: a name already taken, a VM that must be stopped (409).",
    "too_large": "The upload or body is larger than allowed (413).",
    "locked": "Another operation holds the object (423).",
    "too_many_requests": "Too many attempts: wait before trying again (429).",
    "internal": "An error of the server: its log has the details (500).",
    "not_implemented": "Not supported on this node (501).",
    "peer_error": "Another member of the cluster did not answer or failed (502).",
    "unavailable": "The node cannot do this right now (503).",
    "timeout": "Something the request waited for did not answer in time (504).",
    # Precise codes.
    "api_gone": "An address of the API before 1.0: the detail names the one to use (410).",
    "changed_elsewhere": "Another node of the cluster changed the same thing first: nothing was applied (409).",
    "not_confirmed": "The cluster did not take the change in time, or refused it: it is copied when it answers (503).",
    "read_only": "This node changes nothing right now (no quorum, a cluster update): the detail says why (503).",
    "cluster_unavailable": "The cluster's configuration service does not answer: nothing new can be created (503).",
}

BY_STATUS = {
    400: "invalid",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "api_gone",
    413: "too_large",
    422: "invalid",
    423: "locked",
    429: "too_many_requests",
    500: "internal",
    501: "not_implemented",
    502: "peer_error",
    503: "unavailable",
    504: "timeout",
}


def for_status(status):
    """The code of an error that named none: its status's, or the nearest class's."""
    return BY_STATUS.get(status) or ("internal" if status >= 500 else "invalid")
