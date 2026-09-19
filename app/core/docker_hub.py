"""Docker Hub image search: a server-side proxy for the public Docker Hub
search API, so that the container creation form can offer a keyword search
("apache", "nginx", "postgres"...) instead of requiring the exact image name
in advance (e.g. the Apache HTTP server is called "httpd" on Docker Hub, not
"apache", so a keyword search is needed to find it).

urllib (standard library) rather than an extra HTTP dependency
(requests/httpx are not used in this project): consistent with the rest of
the code, which already shells out to real tools (skopeo/umoci to pull an
image, qemu-img for disks...) without adding a Python dependency for it."""

import json
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://hub.docker.com/v2/search/repositories/"


def search_images(query, limit=15):
    query = (query or "").strip()
    if not query:
        return []
    params = urllib.parse.urlencode({"query": query, "page_size": min(max(limit, 1), 25)})
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers={"User-Agent": "Hyperlite/1.0"})  # noqa: S310 -- URL scheme validated by require_http_url() or a constant https URL
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:  # noqa: S310 -- SEARCH_URL is a constant https URL
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"Docker Hub search unavailable: {e}") from e

    results = []
    for r in data.get("results", []):
        name = r.get("repo_name") or ""
        if not name:
            continue
        results.append(
            {
                "nom": name,
                "description": (r.get("short_description") or "").strip(),
                "etoiles": r.get("star_count", 0),
                "telechargements": r.get("pull_count", 0),
                "officielle": bool(r.get("is_official")),
            }
        )
    # Official images first (e.g. "nginx", "httpd", "postgres": maintained by
    # Docker, the safest choice for a user who just types "apache" without knowing
    # which exact image they want), then by popularity.
    results.sort(key=lambda r: (not r["officielle"], -r["etoiles"]))
    return results
