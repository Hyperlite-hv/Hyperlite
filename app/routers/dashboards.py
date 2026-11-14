"""Dashboards and saved queries (app/core/dashboards.py, docs/design/console-2.md sections 2 and 3)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import dashboards
from app.core.audit import log_action
from app.core.listing import listing
from app.core.security import get_current_user

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
queries_router = APIRouter(prefix="/saved-queries", tags=["dashboards"])


def _answer(fn, *args):
    try:
        return fn(*args)
    except dashboards.DashboardError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    shared: str = "private"
    tiles: list[dict[str, Any]] = Field(default_factory=list)


class DashboardPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    shared: str | None = None
    tiles: list[dict[str, Any]] | None = None


class CopyIn(BaseModel):
    name: str | None = Field(None, max_length=80)


@router.get("")
def list_dashboards(user: dict = Depends(get_current_user)):
    """The dashboards this account reads: its own, those shared with everyone and with its groups."""
    return listing(dashboards.dashboards(user))


@router.post("", status_code=201)
def create_dashboard(payload: DashboardIn, user: dict = Depends(get_current_user)):
    d = _answer(dashboards.create, payload.model_dump(), user)
    log_action(user["username"], "dashboard_create", d["name"], "success", d["shared"])
    return d


@router.get("/{dash_id}")
def read_dashboard(dash_id: str, user: dict = Depends(get_current_user)):
    return _answer(dashboards.get, dash_id, user)


@router.patch("/{dash_id}")
def change_dashboard(dash_id: str, payload: DashboardPatch, user: dict = Depends(get_current_user)):
    """Its name, who it is shared with, its tiles (the whole list, in order)."""
    d = _answer(dashboards.replace, dash_id, payload.model_dump(exclude_none=True), user)
    log_action(user["username"], "dashboard_change", d["name"], "success", d["shared"])
    return d


@router.delete("/{dash_id}")
def delete_dashboard(dash_id: str, user: dict = Depends(get_current_user)):
    d = _answer(dashboards.delete, dash_id, user)
    log_action(user["username"], "dashboard_delete", d["name"], "success")
    return {"message": "Dashboard deleted"}


@router.post("/{dash_id}/copy", status_code=201)
def copy_dashboard(dash_id: str, payload: CopyIn, user: dict = Depends(get_current_user)):
    """A private copy of a dashboard this account reads, to make its own."""
    return _answer(dashboards.copy, dash_id, user, payload.name)


class QueryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    query: str = Field(min_length=1, max_length=500)
    view: str = "list"
    group_by: str | None = None
    shared: str = "private"


class QueryPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=80)
    query: str | None = Field(None, min_length=1, max_length=500)
    view: str | None = None
    group_by: str | None = None
    shared: str | None = None


@queries_router.get("")
def list_queries(user: dict = Depends(get_current_user)):
    return listing(dashboards.queries(user))


@queries_router.post("", status_code=201)
def save_query(payload: QueryIn, user: dict = Depends(get_current_user)):
    q = _answer(dashboards.save_query, payload.model_dump(), user)
    log_action(user["username"], "saved_query_create", q["name"], "success", q["query"])
    return q


@queries_router.patch("/{query_id}")
def change_query(query_id: str, payload: QueryPatch, user: dict = Depends(get_current_user)):
    return _answer(dashboards.change_query, query_id, payload.model_dump(exclude_unset=True), user)


@queries_router.delete("/{query_id}")
def delete_query(query_id: str, user: dict = Depends(get_current_user)):
    q = _answer(dashboards.delete_query, query_id, user)
    log_action(user["username"], "saved_query_delete", q["name"], "success")
    return {"message": "Saved query deleted"}
