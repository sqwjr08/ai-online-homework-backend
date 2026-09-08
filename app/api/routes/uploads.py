from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.config import get_settings
from app.core.deps import require_roles
from app.models import User, UserRole

router = APIRouter(prefix="/uploads", tags=["uploads"])

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}


@router.post("/images")
async def upload_image(
    file: UploadFile = File(...),
    _: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> dict[str, str]:
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported image type")

    settings = get_settings()
    upload_dir = Path(settings.upload_dir) / "images"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{ALLOWED_IMAGE_TYPES[file.content_type]}"
    target = upload_dir / filename
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image is too large")
    target.write_bytes(content)
    url = f"{settings.public_base_url.rstrip('/')}/uploads/images/{filename}"
    return {"url": url}
