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
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = get_user(username)
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
