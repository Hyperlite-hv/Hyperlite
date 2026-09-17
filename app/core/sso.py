"""SSO OIDC (chantier 20, 2026-09-17) -- authentification deleguee a un
fournisseur d'identite externe (IdP) via OpenID Connect (Authorization
Code flow), EN PLUS de l'authentification locale existante, jamais a sa
place (decision explicite d'Antho : le compte admin local doit rester
utilisable en secours si l'IdP est injoignable ou mal configure -- sans
ca, une mauvaise config SSO verrouillerait tout le serveur).

Aucune nouvelle dependance : urllib (stdlib, meme convention que
app/core/notifications.py, chantier 28) pour les appels HTTP vers l'IdP,
python-jose (deja utilise par app/core/security.py pour les JWT de
session Hyperlite) pour valider la signature de l'ID token renvoye par
l'IdP.

Mapping de roles (decision explicite d'Antho) : le modele de roles de ce
projet est BINAIRE au niveau global (`users.role` : 'admin' ou
'observateur' seulement, voir app/core/database.py -- l'acces fin passe
par les ACL/groupes/roles personnalises de app/core/permissions.py, pas
par ce role global). Un compte SSO recoit 'admin' si l'un de ses groupes
IdP (nom de claim configurable, defaut "groups") figure dans la liste
"groupes admin" configuree cote Hyperlite -- sinon 'observateur' (le plus
restrictif). Le role est RE-RESOLU a CHAQUE connexion SSO, pas seulement
a la creation du compte : un utilisateur retire du groupe admin cote IdP
perd ses droits admin Hyperlite des sa prochaine connexion, plutot que de
rester admin indefiniment en local apres coup.
"""
import json
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from jose import jwk as jose_jwk
from jose import jwt as jose_jwt
from jose.exceptions import JWTError

from app.core.database import get_conn
from app.core.security import hash_password

STATE_TTL_S = 600  # 10 min -- le temps de s'authentifier chez l'IdP, pas plus
HTTP_TIMEOUT_S = 10


class LocalAccountConflict(Exception):
    """Leve quand une connexion SSO resout un nom d'utilisateur qui
    appartient DEJA a un compte local (auth_source != 'sso') -- refuse
    plutot que d'ecraser silencieusement son role ou son statut, ce qui
    pourrait sinon retirer les droits admin du compte de secours local."""


def get_config():
    with get_conn() as db:
        row = db.execute("SELECT * FROM sso_config WHERE id = 1").fetchone()
        return dict(row) if row else None


def set_config(**fields):
    if not fields:
        return
    with get_conn() as db:
        existing = db.execute("SELECT id FROM sso_config WHERE id = 1").fetchone()
        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            db.execute(f"UPDATE sso_config SET {sets} WHERE id = 1", list(fields.values()))
        else:
            cols = ["id"] + list(fields.keys())
            placeholders = ", ".join("?" for _ in cols)
            db.execute(f"INSERT INTO sso_config ({', '.join(cols)}) VALUES ({placeholders})", [1] + list(fields.values()))
        db.commit()


def _http_get_json(url):
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_form(url, data):
    body = urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def discover(issuer):
    """Document de decouverte OIDC standard -- un seul champ (l'issuer) a
    saisir cote admin plutot que 4 URLs distinctes (authorization/token/
    jwks/userinfo endpoints), exactement le mecanisme prevu par la spec
    OIDC Discovery (RFC/spec OpenID Connect Discovery 1.0)."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    return _http_get_json(url)


def create_state():
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    now = time.time()
    with get_conn() as db:
        # Purge les etats perimes au passage -- une table aussi ephemere
        # (duree de vie 10 min) ne justifie pas un scheduler dedie.
        db.execute("DELETE FROM sso_login_state WHERE created_at < ?", (now - STATE_TTL_S,))
        db.execute(
            "INSERT INTO sso_login_state (state, nonce, created_at) VALUES (?, ?, ?)",
            (state, nonce, now),
        )
        db.commit()
    return state, nonce


def consume_state(state):
    """A usage UNIQUE -- supprime la ligne des sa lecture (protection
    anti-rejeu standard du flux Authorization Code : un `state` ne doit
    jamais pouvoir servir deux fois)."""
    with get_conn() as db:
        row = db.execute("SELECT nonce, created_at FROM sso_login_state WHERE state = ?", (state,)).fetchone()
        if not row:
            return None
        db.execute("DELETE FROM sso_login_state WHERE state = ?", (state,))
        db.commit()
    if time.time() - row["created_at"] > STATE_TTL_S:
        return None
    return row["nonce"]


def build_authorize_url(config, discovery_doc, state, nonce):
    params = {
        "response_type": "code",
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "scope": config["scope"],
        "state": state,
        "nonce": nonce,
    }
    return discovery_doc["authorization_endpoint"] + "?" + urlencode(params)


def exchange_code(config, discovery_doc, code):
    return _http_post_form(discovery_doc["token_endpoint"], {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config["redirect_uri"],
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
    })


def validate_id_token(config, discovery_doc, id_token, nonce):
    """Verifie la signature (via les JWKS publies par l'IdP, cle
    selectionnee par `kid`) ET les claims standard (issuer, audience,
    expiration -- geres par jose.jwt.decode) ET le nonce (protection
    anti-rejeu specifique a l'ID token, distincte du `state` ci-dessus qui
    protege la redirection elle-meme)."""
    jwks = _http_get_json(discovery_doc["jwks_uri"])
    header = jose_jwt.get_unverified_header(id_token)
    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == header.get("kid")), None)
    if key_data is None:
        raise JWTError("Clé de signature inconnue (kid absent des JWKS de l'IdP)")
    key = jose_jwk.construct(key_data, header.get("alg", "RS256"))
    claims = jose_jwt.decode(
        id_token, key, algorithms=[header.get("alg", "RS256")],
        audience=config["client_id"], issuer=config["issuer"],
    )
    if claims.get("nonce") != nonce:
        raise JWTError("nonce invalide (rejeu possible)")
    return claims


def resolve_role(config, claims):
    admin_groups = {g.strip() for g in (config.get("admin_groups") or "").split(",") if g.strip()}
    if not admin_groups:
        return "observateur"
    user_groups = set(claims.get(config.get("group_claim") or "groups") or [])
    return "admin" if user_groups & admin_groups else "observateur"


def resolve_username(claims):
    return claims.get("preferred_username") or claims.get("email") or claims.get("sub")


def provision_user(username, role):
    """Cree le compte SSO s'il n'existe pas encore, ou met a jour son
    role a CHAQUE connexion (voir docstring du module). Mot de passe
    local rendu structurellement inutilisable (secret aleatoire hache,
    jamais communique nulle part) : ce compte ne peut s'authentifier que
    via SSO. Leve LocalAccountConflict si ce nom appartient deja a un
    compte local -- ne JAMAIS ecraser silencieusement un compte existant
    non-SSO (voir la docstring de l'exception)."""
    with get_conn() as db:
        existing = db.execute(
            "SELECT username, role, auth_source FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing is not None and existing["auth_source"] != "sso":
            raise LocalAccountConflict(username)
        if existing is None:
            db.execute(
                "INSERT INTO users (username, hashed_password, role, auth_source) VALUES (?, ?, ?, 'sso')",
                (username, hash_password(secrets.token_hex(32)), role),
            )
        else:
            db.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        db.commit()
        row = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row)
