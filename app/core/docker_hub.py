"""Recherche d'images sur Docker Hub (chantier 18, 2026-09-13) : proxy cote
serveur de l'API publique de recherche Docker Hub, pour que le formulaire de
creation de conteneur propose une recherche par mot-cle ("apache", "nginx",
"postgres"...) plutot que d'exiger de connaitre a l'avance le nom exact de
l'image (ex. le serveur HTTP Apache s'appelle "httpd" sur Docker Hub, pas
"apache" -- une recherche par mot-cle est necessaire pour le trouver).

urllib (bibliotheque standard) plutot qu'une dependance HTTP supplementaire
(requests/httpx, absentes de ce projet) : coherent avec le reste du code,
qui shell-oute deja vers de vrais outils (skopeo/umoci pour tirer une image,
qemu-img pour les disques...) sans jamais ajouter de dependance Python pour
ca."""
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
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers={"User-Agent": "Hyperlite/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"Recherche Docker Hub indisponible : {e}")

    results = []
    for r in data.get("results", []):
        name = r.get("repo_name") or ""
        if not name:
            continue
        results.append({
            "nom": name,
            "description": (r.get("short_description") or "").strip(),
            "etoiles": r.get("star_count", 0),
            "telechargements": r.get("pull_count", 0),
            "officielle": bool(r.get("is_official")),
        })
    # Officielles d'abord (ex. "nginx", "httpd", "postgres" -- maintenues
    # par Docker, le choix le plus sur pour un utilisateur qui tape juste
    # "apache" sans savoir quelle image precise il veut), puis par
    # popularite.
    results.sort(key=lambda r: (not r["officielle"], -r["etoiles"]))
    return results
