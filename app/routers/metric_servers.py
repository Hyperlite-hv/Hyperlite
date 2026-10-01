"""External metric servers the collector pushes to (app/core/metric_export.py). Administrators only: a server
definition decides where this cluster's figures go."""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core import metric_export
from app.core.audit import log_action
from app.core.security import require_role

router = APIRouter(prefix="/metric-servers", tags=["metrics"])


class MetricServer(BaseModel):
    nom: str
    type: str
    actif: bool = True
    url: str | None = None
    org: str | None = None
    bucket: str | None = None
    jeton: str | None = None  # write-only; empty on an update keeps the stored one
    hote: str | None = None
    port: int | None = None
    prefixe: str | None = None


@router.get("")
def list_metric_servers(user: dict = Depends(require_role("admin"))):
    return metric_export.list_servers()


@router.post("", status_code=201)
def create_metric_server(payload: MetricServer, user: dict = Depends(require_role("admin"))):
    try:
        server = metric_export.save_server(payload.model_dump())
    except metric_export.ExportError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], "create_metric_server", server["nom"], "succes", server["type"])
    return server


@router.put("/{server_id}")
def update_metric_server(server_id: int, payload: MetricServer, user: dict = Depends(require_role("admin"))):
    if metric_export.get_server(server_id) is None:
        raise HTTPException(status_code=404, detail="Metric server not found")
    try:
        server = metric_export.save_server(payload.model_dump(), server_id)
    except metric_export.ExportError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    log_action(user["username"], "update_metric_server", server["nom"], "succes")
    return server


@router.delete("/{server_id}")
def delete_metric_server(server_id: int, user: dict = Depends(require_role("admin"))):
    if not metric_export.delete_server(server_id):
        raise HTTPException(status_code=404, detail="Metric server not found")
    log_action(user["username"], "delete_metric_server", str(server_id), "succes")
    return {"message": "Metric server deleted"}


def _latest_rows():
    from app.services import metrics_service

    return metrics_service.latest_tick()


@router.post("/{server_id}/test")
def test_metric_server(server_id: int, user: dict = Depends(require_role("admin"))):
    """Send the latest samples now, to check the address, the credentials and the firewall in one go."""
    server = metric_export.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Metric server not found")
    rows = _latest_rows() or [("host", "host", None, None, None, None, None, None, None)]
    try:
        metric_export.send(server, rows, time.time())
    except Exception as e:  # the cause (DNS, refused, 401...) is what the administrator needs to see
        metric_export.record_result(server_id, e)
        raise HTTPException(status_code=502, detail=f"Sending failed: {e}") from e
    metric_export.record_result(server_id, None)
    return {"envoyes": len(rows)}
