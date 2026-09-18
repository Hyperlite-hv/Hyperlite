import os
from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.database import get_conn

SECRET_KEY = os.environ.get("HYPERLITE_SECRET_KEY", "dev-" + os.urandom(16).hex())
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 240  # 4h -- une session de travail/test longue faisait expirer le jeton en silence (60 min), ex. un upload ISO qui echoue a l'etape finale sans message clair

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)


def hash_password(plain):
    return pwd_context.hash(plain)


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_preauth_token(username: str):
    """Chantier 30 (2FA) : jeton intermediaire emis apres un mot de passe
    correct mais AVANT verification du code TOTP -- prouve seulement "ce
    mot de passe est le bon", pas "cet utilisateur est authentifie".
    Duree de vie courte (5 min, le temps de taper un code) et marque
    explicitement `2fa_pending` : get_current_user() rejette ce claim pour
    qu'un jeton intermediaire vole/intercepte ne puisse jamais servir de
    jeton de session complet."""
    to_encode = {"sub": username, "2fa_pending": True}
    expire = datetime.now(timezone.utc) + timedelta(minutes=5)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_user(username: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def authenticate_user(username: str, password: str):
    user = get_user(username)
    if not user or not verify_password(password, user["hashed_password"]):
        return None
    return user


async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Identifiants invalides",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        payload = None

    if payload is not None:
        username = payload.get("sub")
        # 2fa_pending : jeton intermediaire du chantier 30 (voir
        # create_preauth_token), prouve le mot de passe mais pas le 2FA --
        # ne doit jamais etre accepte comme un jeton de session normal.
        if username is None or payload.get("2fa_pending"):
            raise credentials_exception
        user = get_user(username)
        if user is None:
            raise credentials_exception
        return user

    # Pas un JWT valide : peut etre un jeton API (chantier 30, 2026-09-17)
    # plutot qu'un jeton de session -- meme en-tete Authorization: Bearer,
    # format different (prefixe "hlt_"), donc pas de nouvelle dependance
    # FastAPI a brancher partout, juste un repli ici.
    from app.core.api_tokens import verify_token  # import tardif : evite un cycle (api_tokens -> database, pas de retour vers security)

    user = verify_token(token)
    if user is None:
        raise credentials_exception
    return user


def require_role(*roles):
    async def checker(user: dict = Depends(get_current_user)):
        if user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Rôle '{user['role']}' non autorisé pour cette action",
            )
        return user
    return checker


def require_vm_privilege(privilege):
    """Comme require_role, mais verifie un privilege scope a la VM cible (voir
    app/core/permissions.py) plutot qu'un role global : un admin passe
    toujours, un observateur garde son acces vm.view global, et un
    utilisateur/groupe avec une ACL sur cette VM (ou un pool qui la contient)
    obtient les privileges de son role scope. Le nom du parametre de chemin
    doit etre `name` (comme sur toutes les routes /vms/{name}/...)."""
    from app.core.permissions import has_privilege  # import tardif : evite un cycle avec permissions.py

    async def checker(name: str, user: dict = Depends(get_current_user)):
        if not has_privilege(user, name, privilege):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Droits insuffisants sur la VM '{name}' (privilège requis : {privilege})",
            )
        return user
    return checker


def require_container_privilege(privilege):
    """Equivalent de require_vm_privilege() pour les conteneurs LXC
    (backlog 2026-09-18) -- voir app/core/permissions.py::has_container_privilege."""
    from app.core.permissions import has_container_privilege  # import tardif : evite un cycle

    async def checker(name: str, user: dict = Depends(get_current_user)):
        if not has_container_privilege(user, name, privilege):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Droits insuffisants sur le conteneur '{name}' (privilège requis : {privilege})",
            )
        return user
    return checker
