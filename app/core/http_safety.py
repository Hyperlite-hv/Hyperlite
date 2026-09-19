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
