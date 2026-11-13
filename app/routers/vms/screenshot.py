"""A VM's screen as a small image, for the console thumbnail of its overview (daemon operation vm.screenshot)."""

import base64

from fastapi import Depends
from fastapi.responses import Response

from app.core import privileged
from app.core.security import require_vm_privilege
from app.routers.vms._shared import router


@router.get("/{name}/screenshot", response_class=Response, responses={200: {"content": {"image/png": {}}}})
def get_vm_screenshot(
    name: str, node: str | None = None, width: int = 480, user: dict = Depends(require_vm_privilege("vm.console"))
):
    """The VM's display now, scaled to `width` pixels at most (64 to 1920), as PNG. 422 when it is not running."""
    remote = node if node and node != "local" else None
    shot = privileged.ask(
        "vm.screenshot", user, "vm_screenshot", name, vm=name, node=remote, max_width=max(64, min(width, 1920))
    )
    return Response(base64.b64decode(shot["png"]), media_type="image/png", headers={"Cache-Control": "no-store"})
