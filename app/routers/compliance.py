"""The compliance review of the VMs already there (app/core/compliance.py)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import compliance
from app.core.audit import log_action
from app.core.security import require_area

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.get("")
def get_compliance(user: dict = Depends(require_area("cluster.view"))):
    return compliance.review()


class FixIn(BaseModel):
    vms: list[str] = Field(min_length=1, max_length=500)


@router.post("/fix")
def fix(payload: FixIn, user: dict = Depends(require_area("cluster.manage"))):
    """Add the required tags each VM lacks; returns them per VM."""
    from app.core.libvirt_utils import open_conn

    conn = open_conn(readonly=True)
    try:
        here = {d.name() for d in conn.listAllDomains()}
    finally:
        conn.close()
    unknown = sorted(set(payload.vms) - here)
    if unknown:
        raise HTTPException(status_code=404, detail=f"Not a VM of this node: {', '.join(unknown)}")
    added = {name: compliance.fix(name) for name in payload.vms}
    for name, tags in added.items():
        if tags:
            log_action(user["username"], "compliance_fix", name, "success", f"tags: {', '.join(tags)}")
    return {"added": added}
