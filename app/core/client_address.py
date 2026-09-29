"""The address a request comes from, for the sign-in locks and the audit log.

By default the direct peer, and no header is trusted: X-Forwarded-For is written by the client, so honouring it
would let anyone pick the address their failures are counted against. Behind a reverse proxy, though, every
request comes from the proxy, and 20 failures from anyone locked everybody out (the per-address lock). The
proxies listed in HYPERLITE_TRUSTED_PROXIES (comma-separated addresses) are therefore trusted to tell the real
client: X-Forwarded-For is read from the right, skipping trusted proxies, and the first other address is the
client's.
"""

import ipaddress
import os


def _parse(value):
    out = set()
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.add(ipaddress.ip_address(item))
        except ValueError:
            continue
    return out


def trusted_proxies():
    return _parse(os.environ.get("HYPERLITE_TRUSTED_PROXIES"))


def _ip(value):
    try:
        return ipaddress.ip_address(value.strip())
    except (ValueError, AttributeError):
        return None


def resolve(peer, forwarded_for, trusted):
    """The client address given the direct peer and the X-Forwarded-For header."""
    peer_ip = _ip(peer)
    if peer_ip is None or peer_ip not in trusted or not forwarded_for:
        return peer
    for hop in reversed(forwarded_for.split(",")):
        hop_ip = _ip(hop)
        if hop_ip is None:
            return peer  # a malformed chain: do not guess
        if hop_ip not in trusted:
            return str(hop_ip)
    return peer


def client_address(request):
    peer = request.client.host if request.client else "unknown"
    return resolve(peer, request.headers.get("x-forwarded-for"), trusted_proxies())
