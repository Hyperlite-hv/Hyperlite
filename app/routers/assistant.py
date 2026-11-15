"""The assistant (app/core/assistant.py, docs/design/console-2.md section 10)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core import assistant
from app.core.audit import log_action
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/assistant", tags=["assistant"])
FORWARDED = ("authorization", "cookie", "x-hyperlite-csrf")


class SettingsIn(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    base_url: str | None = Field(None, max_length=500)
    model: str | None = Field(None, max_length=200)
    api_key: str | None = Field(None, max_length=500)
    log_text: bool | None = None


class Turn(BaseModel):
    role: str
    content: str = Field(max_length=16000)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    history: list[Turn] = Field(default_factory=list, max_length=40)


def _answer(fn, *args):
    try:
        return fn(*args)
    except assistant.AssistantError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


@router.get("/status")
def assistant_status(user: dict = Depends(get_current_user)):
    """Whether the assistant is on, for the console's button."""
    s = assistant.settings()
    return {"enabled": s["enabled"], "provider": s["provider"],
            "model": s["model"] or (assistant.ANTHROPIC_DEFAULT_MODEL if s["provider"] == "anthropic" else "")}  # fmt: skip


@router.get("/settings")
def get_settings(user: dict = Depends(require_role("admin"))):
    return assistant.settings()


@router.put("/settings")
def put_settings(payload: SettingsIn, user: dict = Depends(require_role("admin"))):
    changes = payload.model_dump(exclude_none=True)
    s = _answer(assistant.set_settings, changes)
    log_action(user["username"], "assistant_settings", s["provider"], "success",
               ", ".join(sorted(k for k in changes if k != "api_key")) + (" + key" if "api_key" in changes else ""))  # fmt: skip
    return s


@router.post("/ask")
async def ask(payload: AskIn, request: Request, user: dict = Depends(get_current_user)):
    """An answer built from Hyperlite's data, read with this account's own rights (each tool is a GET on this API made
    with the request's credentials)."""
    import httpx2

    from app.main import app

    headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARDED}
    client_ip = request.client.host if request.client else "127.0.0.1"
    transport = httpx2.ASGITransport(app=app, client=(client_ip, 0))
    async with httpx2.AsyncClient(transport=transport, base_url="http://assistant.internal/api/v1", headers=headers,
                                  timeout=60.0) as api:  # fmt: skip

        async def get(path, params):
            r = await api.get(path, params={k: v for k, v in params.items() if v is not None})
            if r.status_code != 200:
                detail = (
                    r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else ""
                )
                raise assistant.AssistantError(r.status_code, f"{path}: {r.status_code} {detail or ''}".strip())
            return r.json()

        try:
            result = await assistant.ask(payload.question, [t.model_dump() for t in payload.history], get)
        except assistant.AssistantError as e:
            log_action(user["username"], "assistant_ask", "assistant", "failed", e.detail)
            raise HTTPException(status_code=e.status, detail=e.detail) from None
    detail = f"tools: {', '.join(result['tools']) or 'none'}"
    if result["log_text"]:
        detail += f" | {payload.question[:500]}"
    log_action(user["username"], "assistant_ask", "assistant", "success", detail)
    return {"answer": result["answer"], "tools": result["tools"]}
