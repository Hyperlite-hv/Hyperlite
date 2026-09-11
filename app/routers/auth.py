from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.security import authenticate_user, create_access_token, get_current_user
from app.core.audit import log_action

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        log_action(form_data.username, "login", "auth", "echec", "Identifiants invalides")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides")
    token = create_access_token({"sub": user["username"], "role": user["role"]})
    log_action(user["username"], "login", "auth", "succes")
    return {"access_token": token, "token_type": "bearer", "role": user["role"]}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"username": user["username"], "role": user["role"]}
