"""The Terminal (docs/design/workbench.md): sessions of a box with the account's own rights, and their terminal."""

import contextlib

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core import peer_auth, pty_session, workbench
from app.core.audit import log_action
from app.core.client_address import client_address
from app.core.listing import listing
from app.core.security import get_current_user, get_session_user, require_role
from app.core.tasks import create_task, finish_task
from app.daemon.registry import DaemonError

router = APIRouter(prefix="/workbench", tags=["workbench"])


def _answer(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except workbench.WorkbenchError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


@router.get("")
def overview(user: dict = Depends(get_current_user)):
    """Whether the account may open a Terminal here, its open sessions, and the limits that concern it."""
    s = workbench.settings()
    return {
        "allowed": workbench.may_use(user),
        "sessions": workbench.sessions(user["username"]),
        "limits": {k: s[k] for k in ("per_account", "idle_min", "max_hours", "memory_mb", "home_mb")},
        "recording": s["recording"],
    }


class AsScope(BaseModel):
    role: str = Field(min_length=1, max_length=128)
    resource_type: str = Field(min_length=1, max_length=32)
    resource_id: str = Field("*", min_length=1, max_length=256)


class OpenIn(BaseModel):
    as_scope: AsScope | None = Field(None, alias="as")


@router.post("/sessions", status_code=201)
def open_session(payload: OpenIn, request: Request, user: dict = Depends(get_session_user)):
    """A signed-in session only: an API token (a script, another Terminal) does not open Terminals."""
    scope = payload.as_scope.model_dump() if payload.as_scope else None
    if scope and scope["resource_type"] == "cluster":
        scope["resource_id"] = "*"
    row = _answer(workbench.open_session, user, client_address(request), scope)
    log_action(user["username"], "terminal_open", row["id"], "success", f"as {scope['role']}" if scope else None)
    return row


@router.post("/sessions/{sid}/ticket")
def attach_ticket(sid: str, user: dict = Depends(get_session_user)):
    value = _answer(workbench.ticket, sid, user)
    peer_auth.keep_with_ticket(value, workbench.TICKET_TTL_S)
    return {"ticket": value, "expires_in_s": workbench.TICKET_TTL_S}


@router.websocket("/sessions/{sid}/attach")
async def attach(websocket: WebSocket, sid: str):
    value = websocket.query_params.get("ticket")
    taken = workbench.take_ticket(value)
    if taken is None or taken[0] != sid:
        await websocket.close(code=4401)
        return
    peer_auth.take_ticket_credential(value)
    username = taken[1]
    await websocket.accept()
    try:
        cols = int(websocket.query_params.get("cols", 80))
        rows = int(websocket.query_params.get("rows", 24))
        session = await pty_session.open_session("workbench.attach", username, sid=sid, cols=cols, rows=rows)
    except (DaemonError, ValueError) as e:
        await pty_session.say(websocket, getattr(e, "detail", str(e)))
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(code=1011)
        return
    workbench.mark_attached(sid, True)
    task_id = create_task("terminal", sid, node=None, username=username)
    try:
        await session.serve(websocket)
    finally:
        workbench.mark_attached(sid, False)
        finish_task(task_id, "succeeded")
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close()


@router.delete("/sessions/{sid}")
def end_session(sid: str, user: dict = Depends(get_current_user)):
    row = workbench.get(sid)
    if row is None or row["state"] != "open" or (row["account"] != user["username"] and user["role"] != "admin"):
        raise HTTPException(status_code=404, detail="No such open Terminal session")
    why = "ended by its owner" if row["account"] == user["username"] else f"ended by {user['username']}"
    workbench.close(sid, why, by=user["username"])
    return {"message": "Session ended"}


@router.get("/sessions")
def all_sessions(user: dict = Depends(require_role("admin"))):
    """Every open session of this node: account, since, attached or not, the address it was opened from."""
    return listing(workbench.sessions())


class SettingsIn(BaseModel):
    everyone: bool | None = None
    per_account: int | None = None
    per_node: int | None = None
    idle_min: int | None = None
    max_hours: int | None = None
    memory_mb: int | None = None
    cpu_percent: int | None = None
    tasks: int | None = None
    home_mb: int | None = None
    recording: bool | None = None


@router.get("/settings")
def get_settings(user: dict = Depends(require_role("admin"))):
    return workbench.settings()


@router.put("/settings")
def put_settings(payload: SettingsIn, user: dict = Depends(require_role("admin"))):
    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    saved = _answer(workbench.set_settings, changes)
    log_action(user["username"], "terminal_settings", ", ".join(f"{k}={v}" for k, v in changes.items()), "success")
    return saved


# ---- Tools and files (docs/design/workbench.md, sections 8 and 11) ----


def _own_open_session(sid, user):
    row = workbench.get(sid)
    if row is None or row["state"] != "open" or row["account"] != user["username"]:
        raise HTTPException(status_code=404, detail="No such open Terminal session of yours")
    return row


def _daemon(op, user, **args):
    from app.core import privileged

    try:
        return privileged.run(op, user=user["username"], timeout=300, **args)
    except DaemonError as e:
        raise privileged.http_error(e) from None


@router.post("/sessions/{sid}/tools/tofu")
def install_tofu(sid: str, user: dict = Depends(get_current_user)):
    """From the session itself (`hl tofu`) or the console: OpenTofu into the session's home."""
    _own_open_session(sid, user)
    done = _daemon("workbench.install_tofu", user, sid=sid)
    log_action(user["username"], "terminal_tool", sid, "success", done["installed"])
    return {"message": f"{done['installed']} installed in {done['path']}", **done}


@router.post("/sessions/{sid}/files", status_code=201)
async def upload(sid: str, request: Request, path: str, user: dict = Depends(get_current_user)):
    """The request's body, as is, into `path` of the home (20 MB at most)."""
    import base64

    from fastapi.concurrency import run_in_threadpool

    _own_open_session(sid, user)
    body = await request.body()
    if len(body) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="At most 20 MB per file")
    done = await run_in_threadpool(_daemon, "workbench.file_put", user, sid=sid, path=path,
                                   data=base64.b64encode(body).decode())  # fmt: skip
    log_action(user["username"], "terminal_upload", sid, "success", f"{done['path']} ({done['size']} bytes)")
    return done


@router.get("/sessions/{sid}/files")
def download(sid: str, path: str, user: dict = Depends(get_current_user)):
    import base64

    from fastapi.responses import Response

    _own_open_session(sid, user)
    got = _daemon("workbench.file_get", user, sid=sid, path=path)
    log_action(user["username"], "terminal_download", sid, "success", path)
    safe = "".join(c for c in got["name"] if c.isalnum() or c in "._-") or "file"
    return Response(base64.b64decode(got["data"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})  # fmt: skip
