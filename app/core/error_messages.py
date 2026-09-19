"""Categorize technical errors into actionable causes for logs and tasks.

Instead of a bare "failure", this identifies a likely cause (insufficient
resources, name conflict, permissions, network, storage...) while keeping the
original message for technical diagnosis.

The approach is deliberately pragmatic: it matches on message text rather
than on structured libvirt error codes. libvirt reports most system errors
under one generic code (VIR_ERR_SYSTEM_ERROR) with the real errno buried in
the message, so filtering on text is in practice more reliable (and more
portable across libvirt versions) than relying on the code taxonomy.

"""

import errno
import logging

logger = logging.getLogger(__name__)

try:
    import libvirt
except ImportError:  # not installed in some test contexts
    libvirt = None

# Order matters: the first matching pattern wins (e.g. "network" must be tested
# before the generic fallback, but after more specific patterns such as
# "memory" in case both appear).
_PATTERNS = [
    (
        ("remote host identification has changed", "host key verification failed"),
        "The SSH host key of this node changed or is not trusted. If the node was reinstalled, remove it and add it again; otherwise treat this as a possible man-in-the-middle attack",
    ),
    (("cannot allocate memory", "out of memory", "failed to allocate"), "Insufficient host resources (RAM)"),
    (("no space left on device", "not enough free space", "insufficient free space"), "Insufficient host storage"),
    (
        ("permission denied", "access denied", "operation not permitted"),
        "Insufficient permissions on the host (file/libvirt rights)",
    ),
    (
        ("already exists", "already running", "already defined", "already active"),
        "Conflict: the resource already exists or is already active",
    ),
    (("device or resource busy", "resource busy"), "Resource busy (already in use elsewhere on the host)"),
    (
        ("network is unreachable", "no route to host", "network 'default' not found", "network not found"),
        "Network problem (network not found or unreachable)",
    ),
    (("no domain", "domain not found"), "VM not found in libvirt (definition missing or already removed)"),
]

_ERRNO_LABELS = {
    errno.ENOSPC: "Insufficient host storage",
    errno.EACCES: "Insufficient permissions on the host",
    errno.EPERM: "Insufficient permissions on the host",
    errno.ENOMEM: "Insufficient host resources (RAM)",
    errno.EEXIST: "Conflict: the file or resource already exists",
    errno.EBUSY: "Resource busy (already in use elsewhere on the host)",
}


def describe_exception(e: Exception) -> str:
    """Return '<category>: <original message>' when a probable cause is identified,
    otherwise just str(e). The original message is never hidden: the category
    is added to it, not substituted for it."""
    raw = str(e)
    lowered = raw.lower()

    errno_val = getattr(e, "errno", None)
    if errno_val in _ERRNO_LABELS:
        return f"{_ERRNO_LABELS[errno_val]} : {raw}"

    for needles, label in _PATTERNS:
        if any(n in lowered for n in needles):
            return f"{label} : {raw}"

    if libvirt is not None and isinstance(e, libvirt.libvirtError):
        try:
            code, domain = e.get_error_code(), e.get_error_domain()
            return f"libvirt error (code {code}, domain {domain}): {raw}"
        except Exception:
            logger.debug("Ignored exception in describe_exception()", exc_info=True)

    return raw
