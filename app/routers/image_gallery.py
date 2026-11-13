"""Image gallery (#348, app/core/image_gallery.py): the official cloud images, and their download into the imported
disks library, checked against the distribution's checksums."""

from fastapi import APIRouter, Depends, HTTPException

from app.core import image_gallery
from app.core.listing import listing
from app.core.security import get_current_user, require_role

router = APIRouter(prefix="/image-gallery", tags=["vm-disks"])


@router.get("")
def list_images(user: dict = Depends(get_current_user)):
    return listing(image_gallery.catalog())


@router.post("/{image_id}/download", status_code=202)
def download_image(image_id: str, user: dict = Depends(require_role("admin"))):
    """Starts the download as a task (gallery_download); the image lands in the imported disks as <id>-cloud.qcow2,
    replacing an older download of the same image."""
    if image_id not in image_gallery.CATALOG:
        raise HTTPException(status_code=404, detail=f"No image '{image_id}' in the gallery")
    return {
        "task_id": image_gallery.start_download(image_id, user["username"]),
        "file": image_gallery.file_name(image_id),
    }
