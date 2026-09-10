import logging
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 10_000
READ_CHUNK_BYTES = 64 * 1024
IMAGE_TYPES = {"image/jpeg": ("JPEG", ".jpg"), "image/png": ("PNG", ".png")}


async def read_image(file: UploadFile) -> bytes:
    if file.size is not None and file.size > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 5 MiB")
    content = bytearray()
    while True:
        chunk = await file.read(min(READ_CHUNK_BYTES, MAX_IMAGE_BYTES + 1 - len(content)))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image exceeds 5 MiB")


class LimitedBuffer(BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Normalized image exceeds 5 MiB")
        return super().write(data)


def normalize_image(content: bytes, content_type: str) -> bytes:
    expected, _ = IMAGE_TYPES[content_type]
    try:
        with Image.open(BytesIO(content), formats=["JPEG", "PNG"]) as source:
            if source.format != expected:
                raise HTTPException(status_code=400, detail="Image content does not match MIME type")
            width, height = source.size
            if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="Image dimensions exceed limits")
            if getattr(source, "n_frames", 1) != 1:
                raise HTTPException(status_code=400, detail="Animated images are not supported")
            source.verify()
        # Verify alone does not decode JPEG pixels. Load again before accepting or re-encoding.
        with Image.open(BytesIO(content), formats=["JPEG", "PNG"]) as source:
            source.load()
            with ImageOps.exif_transpose(source) as oriented:
                with oriented.convert("RGB" if expected == "JPEG" else "RGBA") as pixels:
                    # A fresh image carries no EXIF, comments, profiles or client-supplied metadata.
                    with Image.frombytes(pixels.mode, pixels.size, pixels.tobytes()) as clean:
                        with LimitedBuffer() as output:
                            clean.save(output, format=expected, **({"quality": 90} if expected == "JPEG" else {}))
                            return output.getvalue()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(status_code=413, detail="Image dimensions exceed limits") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid or damaged image") from exc


def save_image(content: bytes, content_type: str, upload_dir: Path) -> str:
    normalized = normalize_image(content, content_type)
    directory = upload_dir / "images"
    filename = f"{uuid4().hex}{IMAGE_TYPES[content_type][1]}"
    target = directory / filename
    created = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            created = True
            output.write(normalized)
    except OSError as exc:
        if created:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                logger.exception("Failed to clean up an incomplete image upload")
        raise HTTPException(status_code=503, detail="Image storage unavailable") from exc
    return filename
