"""Guards for outbound HTTP requests whose URL is configured by an administrator
or received from a remote service (webhooks, OIDC discovery documents)."""

from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


def require_http_url(url):
    """Return `url` unchanged if it is an absolute http(s) URL, otherwise raise ValueError.

    urllib.request.urlopen() also opens file:// and ftp:// URLs; without this
    check an attacker-influenced URL could make the server read local files.
    """
    parsed = urlparse(url or "")
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError(f"Unsupported URL (only absolute http:// and https:// URLs are allowed): {url!r}")
    return url


def refuse_link_local(url):
    """`url` unchanged unless its host is, or resolves to, a link-local address (169.254.0.0/16, fe80::/10): the cloud
    providers' metadata services live there, with the host's credentials. Loopback and private addresses stay allowed
    (a local model's endpoint is often one). Raises ValueError."""
    import ipaddress
    import socket

    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return url  # not resolved now: the request itself will fail
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            continue
        if address.is_link_local:
            raise ValueError(f"The address {host} is link-local (a metadata service's): refused")
    return url
