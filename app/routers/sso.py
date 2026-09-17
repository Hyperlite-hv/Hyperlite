"""Endpoints SSO OIDC (chantier 20). Toute la logique vit dans
app/core/sso.py -- ce fichier ne fait que le cablage HTTP : redirections
du flux Authorization Code, et transformation des erreurs (IdP
injoignable, jeton invalide, config incomplete...) en une redirection
vers l'ecran de connexion avec un message clair plutot qu'un 500 brut
que personne ne verrait (ce sont des redirections de NAVIGATEUR, pas des
appels API consommes par le frontend JS)."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from jose.exceptions import JWTError

from app.core import sso
from app.core.audit import log_action
from app.core.security import create_access_token, require_role

router = APIRouter(prefix="/auth/sso", tags=["sso"])


class SSOConfigIn(BaseModel):
    enabled: bool
    issuer: str = ""
    client_id: str = ""
    # None = ne pas modifier le secret existant -- evite de forcer sa
    # ressaisie a chaque edition des autres champs (l'UI ne le reaffiche
    # jamais en clair, voir GET /config ci-dessous).
    client_secret: str | None = None
    redirect_uri: str = ""
    scope: str = "openid profile email groups"
    group_claim: str = "groups"
    admin_groups: str = ""


def _redirect_error(message):
    return RedirectResponse(f"/?sso_error={quote(message)}")


@router.get("/status")
def sso_status():
    """Public, SANS authentification -- l'ecran de connexion doit savoir
    s'il faut afficher le bouton SSO avant que quiconque soit connecte.
    Ne renvoie jamais la config elle-meme (voir /config, reserve admin)."""
    config = sso.get_config()
    enabled = bool(config and config["enabled"] and config["issuer"] and config["client_id"])
    return {"enabled": enabled}


@router.get("/config")
def get_sso_config(user: dict = Depends(require_role("admin"))):
    config = sso.get_config() or {}
    config = dict(config)
    has_secret = bool(config.pop("client_secret", None))
    config["client_secret_set"] = has_secret
    return config


@router.put("/config")
def put_sso_config(payload: SSOConfigIn, user: dict = Depends(require_role("admin"))):
    fields = payload.model_dump(exclude={"client_secret"})
    fields["enabled"] = int(payload.enabled)
    if payload.client_secret:  # None ou "" -> secret existant conserve
        fields["client_secret"] = payload.client_secret
    sso.set_config(**fields)
    log_action(user["username"], "update_sso_config", "sso", "succes")
    return {"message": "Configuration SSO mise à jour"}


@router.get("/login")
def sso_login():
    config = sso.get_config()
    if not config or not config["enabled"]:
        raise HTTPException(status_code=400, detail="SSO non activé")
    if not (config["issuer"] and config["client_id"] and config["client_secret"] and config["redirect_uri"]):
        raise HTTPException(status_code=400, detail="Configuration SSO incomplète")
    try:
        doc = sso.discover(config["issuer"])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"IdP injoignable : {e}")
    state, nonce = sso.create_state()
    return RedirectResponse(sso.build_authorize_url(config, doc, state, nonce))


@router.get("/callback")
def sso_callback(code: str | None = None, state: str | None = None, error: str | None = None, error_description: str | None = None):
    if error:
        log_action("system", "login", "auth", "echec", f"SSO refusé par l'IdP : {error_description or error}")
        return _redirect_error(error_description or error)
    if not code or not state:
        return _redirect_error("Réponse de l'IdP incomplète")

    nonce = sso.consume_state(state)
    if nonce is None:
        return _redirect_error("Session de connexion expirée, réessayez")

    config = sso.get_config()
    if not config or not config["enabled"]:
        return _redirect_error("SSO désactivé")

    try:
        doc = sso.discover(config["issuer"])
        tokens = sso.exchange_code(config, doc, code)
        id_token = tokens["id_token"]
        claims = sso.validate_id_token(config, doc, id_token, nonce)
    except (JWTError, KeyError) as e:
        log_action("system", "login", "auth", "echec", f"SSO : jeton d'identité invalide ({e})")
        return _redirect_error("Jeton d'identité invalide")
    except Exception as e:
        log_action("system", "login", "auth", "echec", f"SSO : IdP injoignable ou réponse invalide ({e})")
        return _redirect_error("Impossible de contacter l'IdP")

    username = sso.resolve_username(claims)
    if not username:
        return _redirect_error("L'IdP n'a fourni aucun identifiant exploitable")
    role = sso.resolve_role(config, claims)

    try:
        db_user = sso.provision_user(username, role)
    except sso.LocalAccountConflict:
        log_action(username, "login", "auth", "echec", "SSO : ce nom correspond déjà à un compte local")
        return _redirect_error("Ce nom d'utilisateur correspond déjà à un compte local")

    token = create_access_token({"sub": db_user["username"], "role": db_user["role"]})
    log_action(db_user["username"], "login", "auth", "succes", "Connexion SSO")
    return RedirectResponse(f"/?sso_token={token}")
