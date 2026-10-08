"""One reading of a node's VMs, shared by the requests that need it at the same moment.

Listing the VMs reads every VM's definition from libvirt and parses it: about 0.2 ms each, 0.2 s for 1,000 VMs. The
dashboard asks for the VM list and the network list (which counts the VMs on each network) every 6 s, in every open
tab, so with five tabs open on 1,000 VMs the same thousand definitions were read ten times every 6 s, and the VM list
went past 0.3 s. Now one reading serves every request of the next TTL_S seconds, and requests arriving while it runs
wait for it instead of starting their own.

Any change made through Hyperlite drops what is kept (audit.log_action, and every request that is not a read): a VM
just created, started or edited is listed as it is at once. A change made outside Hyperlite (a guest shutting itself
down, virsh) shows within TTL_S seconds.
"""

import threading
import time

TTL_S = 2.0

_lock = threading.Lock()
_generation = 0  # moved on by every invalidate(): a reading started before it is not kept
_entries = {}  # key -> (generation, monotonic time, value)
_key_locks = {}


def invalidate():
    global _generation
    with _lock:
        _generation += 1
        _entries.clear()


def get(key, compute):
    """compute() for this key, or what a reading of the last TTL_S seconds returned. The value is shared: callers
    must copy what they change."""
    with _lock:
        key_lock = _key_locks.setdefault(key, threading.Lock())
    with key_lock:  # one reading per key at a time: the others wait for it, then take it
        with _lock:
            entry = _entries.get(key)
            if entry and entry[0] == _generation and time.monotonic() - entry[1] < TTL_S:
                return entry[2]
            generation = _generation
        value = compute()
        with _lock:
            if generation == _generation:
                _entries[key] = (generation, time.monotonic(), value)
        return value
