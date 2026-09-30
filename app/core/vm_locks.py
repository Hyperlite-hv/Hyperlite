"""One operation at a time on a VM's disks or definition.

Backups, restores, snapshots, migrations, disk moves and automatic deletion all
run in background threads. Without a per-VM claim, two of them could work on the
same VM at once: a restore writing the disks a live backup is merging, an
automatic deletion removing a VM while it is being restored. A second operation
is refused at once (HTTP 409 at the endpoints) rather than queued: a silent wait
behind a long copy would look like a hang, and replaying a stale request later
is rarely what the user still wants.

The claim is taken synchronously by the endpoint, so the refusal reaches the
caller, and released by the background thread when the work ends:

    claim = vm_locks.claim(name, "Restore")      # raises VmBusy
    threading.Thread(target=job, args=(claim,)).start()
    ...
    def job(claim):
        with claim:                              # released on exit, whatever happens
            ...
"""

import threading

_registry_lock = threading.Lock()
_held = {}  # (node, vm name) -> label of the running operation


class VmBusy(RuntimeError):
    def __init__(self, name, running):
        super().__init__(f"VM '{name}' is busy: {running} is in progress. Try again when it has finished.")
        self.running = running


def _key(name, node):
    return (node or "local", name)


class Claim:
    """A claim on a VM, released exactly once (by `release()` or by leaving a `with` block)."""

    def __init__(self, key):
        self._key = key
        self._released = False
        self._lock = threading.Lock()

    def release(self):
        with self._lock:
            if self._released:
                return
            self._released = True
        with _registry_lock:
            _held.pop(self._key, None)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.release()
        return False


def claim(name, what, node=None):
    """Claim VM `name` (on `node`, the local host by default) for operation `what`.
    Raises VmBusy when another operation holds it."""
    key = _key(name, node)
    with _registry_lock:
        running = _held.get(key)
        if running is not None:
            raise VmBusy(name, running)
        _held[key] = what
    return Claim(key)


def running(name, node=None):
    """Label of the operation holding the VM, or None."""
    with _registry_lock:
        return _held.get(_key(name, node))


def busy_on_node(node):
    """Labels of the operations running on VMs of `node` (the local host for None)."""
    with _registry_lock:
        return [what for (n, _vm), what in _held.items() if n == (node or "local")]


def claim_or_409(name, what, node=None):
    """claim() for an endpoint: a busy VM becomes an HTTP 409 with the reason."""
    from fastapi import HTTPException

    try:
        return claim(name, what, node)
    except VmBusy as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


def released_after(held, target):
    """`target` wrapped so that `held` is released when it returns or raises: what a background thread runs."""

    def body(*args, **kwargs):
        with held:
            return target(*args, **kwargs)

    return body
