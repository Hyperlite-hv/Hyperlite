"""VM API routes, split by area.

Every submodule registers its routes on the shared ``router`` (see ``_shared``).
The import order below is the route registration order and must stay as is.
"""

# isort: skip_file
from app.routers.vms._shared import router
from app.routers.vms import inventory  # noqa: F401
from app.routers.vms import settings  # noqa: F401
from app.routers.vms import create  # noqa: F401
from app.routers.vms import lifecycle  # noqa: F401
from app.routers.vms import auto_cleanup  # noqa: F401
from app.routers.vms import devices  # noqa: F401
from app.routers.vms import firewall  # noqa: F401
from app.routers.vms import snapshots  # noqa: F401
from app.routers.vms import clone  # noqa: F401
from app.routers.vms import migration  # noqa: F401
from app.routers.vms import runtime  # noqa: F401
from app.routers.vms import console  # noqa: F401

__all__ = ["router"]
