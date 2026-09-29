"""Security headers sent with every response.

The session token lives in the browser's localStorage and the service runs as
root, so a script injected into the dashboard would act with root-equivalent
power over the host. The Content-Security-Policy is the defence in depth
against that: only scripts served by Hyperlite itself run, plus the inline
scripts of the built index.html, allowed by their exact hash (computed from the
file, so a rebuild never needs this code to change).
"""

import base64
import hashlib
import os
import re

_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\b[^>]*>", re.IGNORECASE | re.DOTALL)
# What may appear in a Host header that is echoed into the policy (host name, IPv4, [IPv6], port).
_HOST_RE = re.compile(r"[A-Za-z0-9.\-:\[\]]{1,260}")

# Paths of the optional interactive API documentation: Swagger UI and ReDoc load
# their code from a CDN, so they are left out of the policy (and only served when
# enabled, see api_docs_enabled()).
API_DOC_PATHS = ("/docs", "/redoc")


def inline_script_hashes(index_html_path):
    """CSP source expressions ('sha256-...') of the inline scripts of `index_html_path`, empty when absent."""
    try:
        with open(index_html_path, encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        return []
    hashes = []
    for body in _INLINE_SCRIPT_RE.findall(html):
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
        hashes.append(f"'sha256-{digest}'")
    return hashes


def content_security_policy(script_hashes, host=None):
    script_src = " ".join(["'self'", *script_hashes])
    # 'self' covers same-origin WebSockets in current browsers; the explicit ws(s)://host
    # keeps the consoles working on older ones.
    connect_src = "'self'"
    if host and _HOST_RE.fullmatch(host):
        connect_src += f" wss://{host} ws://{host}"
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {script_src}",
            # Inline styles: React style props, and the <style> elements xterm.js injects for its theme.
            "style-src 'self' 'unsafe-inline'",
            # The fonts are bundled with the dashboard: an appliance often has no Internet access.
            "font-src 'self' data:",
            "img-src 'self' data: blob:",
            f"connect-src {connect_src}",
            "worker-src 'self' blob:",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def hsts_max_age():
    """HYPERLITE_HSTS_MAX_AGE in seconds, or None (the default).

    Off unless asked for: with the self-signed certificate most installations use,
    Strict-Transport-Security would forbid the browser from letting users past the
    certificate warning, locking them out until the policy expires."""
    raw = (os.environ.get("HYPERLITE_HSTS_MAX_AGE") or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        return None
    return int(raw)


def api_docs_enabled():
    """HYPERLITE_API_DOCS=1 serves /docs, /redoc and /openapi.json. Off by default: they
    answer without authentication and hand out a complete map of the API."""
    return (os.environ.get("HYPERLITE_API_DOCS") or "").strip().lower() in ("1", "true", "yes", "on")


def security_headers(path, scheme, host, script_hashes):
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    }
    if not path.startswith(API_DOC_PATHS):
        headers["Content-Security-Policy"] = content_security_policy(script_hashes, host)
    max_age = hsts_max_age()
    if max_age and scheme == "https":
        headers["Strict-Transport-Security"] = f"max-age={max_age}"
    return headers
