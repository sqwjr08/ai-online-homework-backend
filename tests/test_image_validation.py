from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image, PngImagePlugin

from app.services import images


def image_bytes(format="PNG", size=(3, 2), **kwargs):
    output = BytesIO()
    with Image.new("RGB", size, "red") as source:
        source.save(output, format=format, **kwargs)
    return output.getvalue()


@pytest.mark.parametrize("format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png")])
def test_normalized_image_strips_metadata_and_trailing_content(format, mime):
    info = PngImagePlugin.PngInfo()
    info.add_text("Private", "SECRET_METADATA")
    kwargs = {"pnginfo": info} if format == "PNG" else {"comment": b"SECRET_METADATA"}
    result = images.normalize_image(image_bytes(format, **kwargs) + b"TRAILING_SCRIPT", mime)
    assert b"SECRET_METADATA" not in result and b"TRAILING_SCRIPT" not in result
    with Image.open(BytesIO(result)) as decoded:
        decoded.load()
        assert decoded.format == format and decoded.size == (3, 2)


def test_corrupt_empty_truncated_and_mismatched_images():
    for content in [b"", b"<script>bad</script>", image_bytes()[:-15], image_bytes("JPEG"), image_bytes("GIF")]:
        with pytest.raises(HTTPException) as exc:
            images.normalize_image(content, "image/png")
        assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        images.normalize_image(image_bytes("JPEG")[:-15], "image/jpeg")
    assert exc.value.status_code == 400


def test_pixel_side_and_encoded_size_limits(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(images, "MAX_IMAGE_PIXELS", 5)
        with pytest.raises(HTTPException) as exc:
            images.normalize_image(image_bytes(), "image/png")
        assert exc.value.status_code == 413
    with monkeypatch.context() as patch:
        patch.setattr(images, "MAX_IMAGE_SIDE", 2)
        with pytest.raises(HTTPException) as exc:
            images.normalize_image(image_bytes(), "image/png")
        assert exc.value.status_code == 413
    with monkeypatch.context() as patch:
        patch.setattr(images, "MAX_IMAGE_BYTES", 10)
        with pytest.raises(HTTPException) as exc:
            images.normalize_image(image_bytes(), "image/png")
        assert exc.value.status_code == 413


def test_animated_png_is_rejected():
    output = BytesIO()
    with Image.new("RGB", (2, 2), "red") as first, Image.new("RGB", (2, 2), "blue") as second:
        first.save(output, format="PNG", save_all=True, append_images=[second])
    with pytest.raises(HTTPException) as exc:
        images.normalize_image(output.getvalue(), "image/png")
    assert exc.value.status_code == 400


async def test_read_stops_at_limit_plus_one(monkeypatch):
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 16)
    monkeypatch.setattr(images, "READ_CHUNK_BYTES", 8)

    class File:
        size = None

        def __init__(self, count):
            self.stream = BytesIO(b"x" * count)

        async def read(self, size):
            assert 0 < size <= 8
            return self.stream.read(size)

    file = File(100)
    with pytest.raises(HTTPException) as exc:
        await images.read_image(file)
    assert exc.value.status_code == 413 and file.stream.tell() == 17
    assert await images.read_image(File(16)) == b"x" * 16
    file = File(100)
    file.size = 100
    with pytest.raises(HTTPException):
        await images.read_image(file)
    assert file.stream.tell() == 0


def test_failed_write_removes_partial_file_and_collision_preserves_existing(tmp_path, monkeypatch):
    real_open = Path.open

    class FailingWriter:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def write(self, data):
            self.file.write(data[:4])
            raise OSError("Simulated disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", lambda self, *args, **kwargs: FailingWriter(real_open(self, *args, **kwargs)))
        with pytest.raises(HTTPException) as exc:
            images.save_image(image_bytes(), "image/png", tmp_path)
        assert exc.value.status_code == 503
    assert list((tmp_path / "images").iterdir()) == []
    monkeypatch.setattr(images, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    images.save_image(image_bytes(), "image/png", tmp_path)
    existing = (tmp_path / "images/fixed.png").read_bytes()
    with pytest.raises(HTTPException) as exc:
        images.save_image(image_bytes(), "image/png", tmp_path)
    assert exc.value.status_code == 503 and (tmp_path / "images/fixed.png").read_bytes() == existing
