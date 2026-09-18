import re
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from jose import jwt, JWTError

from app.core.database import get_conn
from app.core.security import (
    authenticate_user, create_access_token, create_preauth_token, get_current_user,
    require_role, hash_password, verify_password, get_user, SECRET_KEY, ALGORITHM,
)
from app.core.audit import log_action
from app.core.permissions import remove_group_member, get_user_groups
from app.core.twofa import generate_secret, provisioning_uri, qr_code_svg, verify_code
from app.core.api_tokens import create_token, list_tokens, revoke_token

router = APIRouter(prefix="/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{2,32}$")

# --- Protection anti-brute-force sur /auth/login (chantier 11, audit du
# 2026-09-13) --- Trouve a l'audit : l'endpoint n'avait AUCUNE limite de
# tentatives, un mot de passe se laissait deviner par essais illimites.
# En memoire (pas en base) : suffisant pour ralentir un brute-force en
# pratique, mais se reinitialise a chaque redemarrage du service -- limite
# connue, documentee plutot que cachee. Une version persistante (table SQLite)
# serait le prochain pas si ce point s'avere insuffisant en usage reel.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_S = 300  # fenetre glissante sur laquelle les echecs comptent
_login_failures = {}  # username -> [timestamps des echecs recents]

# Rate-limiting PAR IP (2026-09-18, backlog du chantier 11 -- "reste a
# explorer" note a l'epoque). Le verrou par COMPTE ci-dessus ne protege
# pas contre un attaquant qui essaie plein de NOMS D'UTILISATEUR
# differents depuis la meme source (le verrou par compte ne se declenche
# jamais si chaque compte n'est tente qu'une ou deux fois) -- un verrou
# par IP, plus large (plus de tentatives tolerees : une IP peut
# legitimement porter plusieurs utilisateurs derriere un NAT/proxy),
# couvre ce cas distinct. Meme mecanisme en memoire, meme limite connue
# (reinitialise au redemarrage).
LOGIN_IP_MAX_ATTEMPTS = 20
LOGIN_IP_WINDOW_S = 300
_login_failures_by_ip = {}  # ip -> [timestamps des echecs recents]


def _login_locked_out(username):
    now = time.time()
    recent = [t for t in _login_failures.get(username, []) if now - t < LOGIN_WINDOW_S]
    _login_failures[username] = recent
    return len(recent) >= LOGIN_MAX_ATTEMPTS


def _login_record_failure(username):
    _login_failures.setdefault(username, []).append(time.time())


def _client_ip(request: Request):
    return request.client.host if request.client else "inconnu"


def _login_ip_locked_out(ip):
    now = time.time()
    recent = [t for t in _login_failures_by_ip.get(ip, []) if now - t < LOGIN_IP_WINDOW_S]
    _login_failures_by_ip[ip] = recent
    return len(recent) >= LOGIN_IP_MAX_ATTEMPTS


def _login_ip_record_failure(ip):
    _login_failures_by_ip.setdefault(ip, []).append(time.time())


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "observateur"


class UserUpdate(BaseModel):
    password: str | None = None
    role: str | None = None


class Login2FA(BaseModel):
    pre_auth_token: str
    code: str


class TwoFAConfirm(BaseModel):
    code: str


class TwoFADisable(BaseModel):
    password: str


class TokenCreate(BaseModel):
    name: str


@router.post("/login")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    ip = _client_ip(request)
    if _login_ip_locked_out(ip):
        log_action(form_data.username, "login", "auth", "echec", f"IP verrouillée ({LOGIN_IP_MAX_ATTEMPTS} échecs récents depuis {ip})")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives échouées depuis cette adresse, réessayez dans {LOGIN_IP_WINDOW_S // 60} minutes",
        )
    if _login_locked_out(form_data.username):
        log_action(form_data.username, "login", "auth", "echec", f"Verrouillé ({LOGIN_MAX_ATTEMPTS} échecs récents)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives échouées pour ce compte, réessayez dans {LOGIN_WINDOW_S // 60} minutes",
        )
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        _login_record_failure(form_data.username)
        _login_ip_record_failure(ip)
        log_action(form_data.username, "login", "auth", "echec", "Identifiants invalides")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides")
    _login_failures.pop(form_data.username, None)

    # Chantier 30 (2FA, 2026-09-17) : mot de passe correct mais 2FA active
    # -- pas de jeton de session complet tout de suite, juste un jeton
    # intermediaire de 5 min (voir create_preauth_token) que le frontend
    # echange contre le vrai jeton via /auth/login/2fa apres le code TOTP.
    if user["totp_enabled"]:
        pre_auth = create_preauth_token(user["username"])
        log_action(user["username"], "login", "auth", "succes", "Mot de passe validé, code 2FA requis")
        return {"require_2fa": True, "pre_auth_token": pre_auth}

    token = create_access_token({"sub": user["username"], "role": user["role"]})
    log_action(user["username"], "login", "auth", "succes")
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}


@router.post("/login/2fa")
def login_2fa(request: Request, payload: Login2FA):
    """Deuxieme etape du login quand /auth/login a renvoye require_2fa.
    Reutilise le meme verrou anti-brute-force que /auth/login (par
    username ET par IP) -- un code TOTP est a 6 chiffres (1M
    combinaisons), pas negligeable a laisser deviner sans limite."""
    ip = _client_ip(request)
    if _login_ip_locked_out(ip):
        log_action("system", "login", "auth", "echec", f"IP verrouillée ({LOGIN_IP_MAX_ATTEMPTS} échecs récents depuis {ip})")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives échouées depuis cette adresse, réessayez dans {LOGIN_IP_WINDOW_S // 60} minutes",
        )
    try:
        claims = jwt.decode(payload.pre_auth_token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Session de connexion expirée, reconnectez-vous")
    username = claims.get("sub")
    if not claims.get("2fa_pending") or not username:
        raise HTTPException(status_code=401, detail="Jeton de pré-authentification invalide")

    if _login_locked_out(username):
        log_action(username, "login", "auth", "echec", f"Verrouillé ({LOGIN_MAX_ATTEMPTS} échecs récents)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives échouées pour ce compte, réessayez dans {LOGIN_WINDOW_S // 60} minutes",
        )

    user = get_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable")
    if not verify_code(user["totp_secret"], payload.code):
        _login_record_failure(username)
        _login_ip_record_failure(ip)
        log_action(username, "login", "auth", "echec", "Code 2FA invalide")
        raise HTTPException(status_code=401, detail="Code invalide")

    _login_failures.pop(username, None)
    token = create_access_token({"sub": user["username"], "role": user["role"]})
    log_action(username, "login", "auth", "succes", "2FA validé")
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {
        "username": user["username"], "role": user["role"], "totp_enabled": bool(user["totp_enabled"]),
        "auth_source": user.get("auth_source", "local"),
    }


# --- 2FA en libre-service (chantier 30, 2026-09-17) -- chaque utilisateur
# gere son propre 2FA, pas besoin d'etre admin (require_role). Flux en 2
# temps : /2fa/setup genere un secret et le stocke DEJA en base mais
# totp_enabled reste a 0 -- tant que /2fa/confirm n'a pas verifie un vrai
# code, la 2FA n'est PAS active, un secret genere puis jamais confirme
# (ex. l'utilisateur ferme l'onglet en scannant le QR) ne bloque personne
# a la prochaine connexion.
@router.post("/2fa/setup")
def setup_2fa(user: dict = Depends(get_current_user)):
    if user["totp_enabled"]:
        raise HTTPException(status_code=400, detail="2FA déjà activée — désactivez-la avant d'en générer une nouvelle")
    secret = generate_secret()
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = ? WHERE username = ?", (secret, user["username"]))
        conn.commit()
    uri = provisioning_uri(secret, user["username"])
    return {"secret": secret, "otpauth_uri": uri, "qr_code_svg": qr_code_svg(uri)}


@router.post("/2fa/confirm")
def confirm_2fa(payload: TwoFAConfirm, user: dict = Depends(get_current_user)):
    fresh = get_user(user["username"])
    if not fresh["totp_secret"]:
        raise HTTPException(status_code=400, detail="Aucune configuration 2FA en attente — lancez /auth/2fa/setup d'abord")
    if not verify_code(fresh["totp_secret"], payload.code):
        raise HTTPException(status_code=401, detail="Code invalide")
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_enabled = 1 WHERE username = ?", (user["username"],))
        conn.commit()
    log_action(user["username"], "enable_2fa", user["username"], "succes")
    return {"message": "2FA activée"}


@router.post("/2fa/disable")
def disable_2fa(payload: TwoFADisable, user: dict = Depends(get_current_user)):
    if not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Mot de passe incorrect")
    with get_conn() as conn:
        conn.execute("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE username = ?", (user["username"],))
        conn.commit()
    log_action(user["username"], "disable_2fa", user["username"], "succes")
    return {"message": "2FA désactivée"}


# --- Jetons API en libre-service (chantier 30, 2026-09-17) -- pense pour
# l'automatisation (scripts/Terraform/cron), authentification alternative
# au JWT de session (voir security.py::get_current_user, repli sur
# api_tokens.verify_token quand le jeton n'est pas un JWT valide).
@router.get("/tokens")
def get_api_tokens(user: dict = Depends(get_current_user)):
    return list_tokens(user["username"])


@router.post("/tokens", status_code=201)
def post_api_token(payload: TokenCreate, user: dict = Depends(get_current_user)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Le nom du jeton est requis")
    token_id, token = create_token(user["username"], name)
    log_action(user["username"], "create_api_token", name, "succes")
    # Le jeton en clair n'est retourne qu'ICI, une seule fois -- il n'est
    # plus jamais recuperable ensuite (seul son hash SHA-256 est stocke).
    return {"id": token_id, "name": name, "token": token}


@router.delete("/tokens/{token_id}")
def delete_api_token(token_id: int, user: dict = Depends(get_current_user)):
    if not revoke_token(user["username"], token_id):
        raise HTTPException(status_code=404, detail="Jeton introuvable")
    log_action(user["username"], "revoke_api_token", str(token_id), "succes")
    return {"message": "Jeton révoqué"}


@router.get("/users")
def list_users(user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        rows = conn.execute("SELECT username, role, auth_source FROM users ORDER BY username").fetchall()
    return [dict(r) for r in rows]


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, user: dict = Depends(require_role("admin"))):
    if not USERNAME_RE.match(payload.username):
        raise HTTPException(status_code=422, detail="Nom d'utilisateur invalide (2-32 caractères : lettres, chiffres, . _ -)")
    if len(payload.password) < 4:
        raise HTTPException(status_code=422, detail="Le mot de passe doit contenir au moins 4 caractères")
    if payload.role not in ("admin", "observateur"):
        raise HTTPException(status_code=422, detail="Rôle invalide (admin ou observateur)")
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO users (username, hashed_password, role) VALUES (?, ?, ?)",
                (payload.username, hash_password(payload.password), payload.role),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=422, detail=f"L'utilisateur '{payload.username}' existe déjà")
    log_action(user["username"], "create_user", payload.username, "succes")
    return {"username": payload.username, "role": payload.role}


@router.patch("/users/{username}")
def update_user(username: str, payload: UserUpdate, user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        existing = conn.execute("SELECT username, role, auth_source FROM users WHERE username = ?", (username,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail=f"Utilisateur '{username}' introuvable")
        # SSO (chantier 20) : le mot de passe local d'un compte SSO est un
        # secret aleatoire jamais communique (voir sso.py::provision_user)
        # -- le "changer" ici donnerait l'illusion trompeuse qu'un login
        # local fonctionnerait ensuite, alors que le role lui-meme sera de
        # toute facon ecrase au prochain login SSO. Le role, lui, reste
        # modifiable manuellement (utile en secours si l'IdP est down).
        if payload.password is not None and existing["auth_source"] == "sso":
            raise HTTPException(status_code=400, detail="Compte SSO : le mot de passe ne peut pas être modifié localement")
        if payload.role is not None:
            if payload.role not in ("admin", "observateur"):
                raise HTTPException(status_code=422, detail="Rôle invalide (admin ou observateur)")
            if existing["role"] == "admin" and payload.role != "admin" and username == user["username"]:
                raise HTTPException(status_code=400, detail="Impossible de te retirer toi-même les droits admin")
            conn.execute("UPDATE users SET role = ? WHERE username = ?", (payload.role, username))
        if payload.password is not None:
            if len(payload.password) < 4:
                raise HTTPException(status_code=422, detail="Le mot de passe doit contenir au moins 4 caractères")
            conn.execute("UPDATE users SET hashed_password = ? WHERE username = ?", (hash_password(payload.password), username))
        conn.commit()
    log_action(user["username"], "update_user", username, "succes")
    return {"message": "Utilisateur mis à jour"}


@router.delete("/users/{username}")
def delete_user(username: str, user: dict = Depends(require_role("admin"))):
    if username == user["username"]:
        raise HTTPException(status_code=400, detail="Impossible de te supprimer toi-même")
    with get_conn() as conn:
        row = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Utilisateur '{username}' introuvable")
        if row["role"] == "admin":
            remaining_admins = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND username != ?", (username,)).fetchone()["n"]
            if remaining_admins == 0:
                raise HTTPException(status_code=400, detail="Impossible de supprimer le dernier compte admin")
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
    for group_id in get_user_groups(username):
        remove_group_member(group_id, username)
    log_action(user["username"], "delete_user", username, "succes")
    return {"message": "Utilisateur supprimé"}
