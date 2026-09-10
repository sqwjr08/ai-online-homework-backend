from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.deps import require_roles
from app.models import User, UserRole
from app.schemas import ImageUploadRead
from app.services.images import IMAGE_TYPES, read_image, save_image

router = APIRouter(prefix="/uploads", tags=["uploads"])

@router.post("/images", response_model=ImageUploadRead, responses={
    400: {"description": "Invalid, damaged, animated or MIME-mismatched image"},
    401: {"description": "Not authenticated"},
    403: {"description": "Teacher or administrator required"},
    413: {"description": "Image exceeds byte or pixel limits"},
    415: {"description": "Only image/jpeg and image/png are supported"},
    503: {"description": "Image storage unavailable"},
})
async def upload_image(
    file: UploadFile = File(...),
    _: User = Depends(require_roles(UserRole.admin, UserRole.teacher)),
) -> ImageUploadRead:
    try:
        if file.content_type not in IMAGE_TYPES:
            raise HTTPException(status_code=415, detail="Only JPEG and PNG are supported")
        settings = get_settings()
        content = await read_image(file)
        filename = await run_in_threadpool(save_image, content, file.content_type, settings.upload_dir)
        return ImageUploadRead(url=f"{settings.public_base_url.rstrip('/')}/uploads/images/{filename}")
    finally:
        await file.close()
