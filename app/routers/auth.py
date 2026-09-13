import re
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.core.database import get_conn
from app.core.security import authenticate_user, create_access_token, get_current_user, require_role, hash_password
from app.core.audit import log_action
from app.core.permissions import remove_group_member, get_user_groups

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


def _login_locked_out(username):
    now = time.time()
    recent = [t for t in _login_failures.get(username, []) if now - t < LOGIN_WINDOW_S]
    _login_failures[username] = recent
    return len(recent) >= LOGIN_MAX_ATTEMPTS


def _login_record_failure(username):
    _login_failures.setdefault(username, []).append(time.time())


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "observateur"


class UserUpdate(BaseModel):
    password: str | None = None
    role: str | None = None


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    if _login_locked_out(form_data.username):
        log_action(form_data.username, "login", "auth", "echec", f"Verrouillé ({LOGIN_MAX_ATTEMPTS} échecs récents)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de tentatives échouées pour ce compte, réessayez dans {LOGIN_WINDOW_S // 60} minutes",
        )
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        _login_record_failure(form_data.username)
        log_action(form_data.username, "login", "auth", "echec", "Identifiants invalides")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides")
    _login_failures.pop(form_data.username, None)
    token = create_access_token({"sub": user["username"], "role": user["role"]})
    log_action(user["username"], "login", "auth", "succes")
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"username": user["username"], "role": user["role"]}


@router.get("/users")
def list_users(user: dict = Depends(require_role("admin"))):
    with get_conn() as conn:
        rows = conn.execute("SELECT username, role FROM users ORDER BY username").fetchall()
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
        existing = conn.execute("SELECT username, role FROM users WHERE username = ?", (username,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail=f"Utilisateur '{username}' introuvable")
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
