"""What an account may do, and why (docs/design/access-2.md, sections 2 and 3): its own effective privileges for the
dashboard, the explanation of one account's rights on one object, and the accounts and groups a delegate assigns to."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core import permissions as perm
from app.core.security import get_current_user
from app.services import account_service

router = APIRouter(prefix="/access", tags=["access"])


@router.get("/me")
def my_access(user: dict = Depends(get_current_user)):
    """The signed-in account's area privileges where this node answers and its privileges per perimeter: the
    dashboard shows the actions it may take from this instead of guessing from the global role."""
    from app.core import cluster_view, self_node

    here = self_node.name()
    nodes = sorted({here, *(m["name"] for m in cluster_view.members())})
    return {"username": user["username"], "role": user["role"], "node": here, "nodes": nodes, **perm.effective(user)}


@router.get("/explain")
def explain(
    resource_type: str = Query(...),
    resource_id: str = Query(...),
    username: str = Query(...),
    user: dict = Depends(get_current_user),
):
    """Each privilege `username` holds on that object and the assignments that grant it. An administrator, or
    access.view on the object or a perimeter above it; any account may ask about itself."""
    if resource_type not in perm.RESOURCE_TYPES:
        raise HTTPException(status_code=422, detail=f"resource_type must be one of: {', '.join(perm.RESOURCE_TYPES)}")
    if username != user["username"] and not perm.may_view_access(user, resource_type, resource_id):
        raise HTTPException(status_code=403, detail="Insufficient rights (required privilege: access.view)")
    target = account_service.get(username)
    if not target:
        raise HTTPException(status_code=404, detail=f"User '{username}' not found")
    return {
        "username": username,
        "role": target["role"],
        "resource_type": resource_type,
        "resource_id": "*" if resource_type == "cluster" else resource_id,
        "perimeters": [
            {"resource_type": t, "resource_id": i} for t, i in perm.perimeters_of(resource_type, resource_id)
        ],
        "privileges": perm.explain(target, resource_type, resource_id),
    }


@router.get("/subjects")
def subjects(user: dict = Depends(get_current_user)):
    """The accounts (not administrators: they hold every right already) and the groups an assignment can name, for
    an administrator or a delegate: GET /users stays an administrator's."""
    if user["role"] != "admin" and not perm.holds_anywhere(user, "access.manage"):
        raise HTTPException(status_code=403, detail="Insufficient rights (required privilege: access.manage)")
    users = [
        {"username": u["username"], "role": u["role"]}
        for u in account_service._store().list_summary()
        if u["role"] != "admin"
    ]
    return {"users": users, "groups": [{"id": g["id"], "name": g["name"]} for g in perm.list_groups()]}
