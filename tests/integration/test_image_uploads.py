from io import BytesIO
from urllib.parse import urlsplit

import pytest
from PIL import Image

from app.models import UserRole

pytestmark = pytest.mark.integration


def png():
    output = BytesIO()
    with Image.new("RGB", (2, 2), "blue") as image:
        image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize("role,format,mime", [(UserRole.teacher, "PNG", "image/png"), (UserRole.admin, "JPEG", "image/jpeg")])
async def test_safe_filename_static_access_and_question_reference(api_client, auth_headers, test_settings, role, format, mime):
    output = BytesIO()
    with Image.new("RGB", (2, 2), "blue") as source:
        source.save(output, format=format)
    headers = await auth_headers(role)
    response = await api_client.post("/api/v1/uploads/images", headers=headers,
                                     files={"file": ("../../private.exe", output.getvalue(), mime)})
    assert response.status_code == 200, response.text
    url = response.json()["url"]
    filename = urlsplit(url).path.rsplit("/", 1)[1]
    assert len(filename.split(".")[0]) == 32 and "private" not in filename
    assert filename.endswith(".png" if format == "PNG" else ".jpg")
    public = await api_client.get(url)
    assert public.status_code == 200 and public.headers["content-type"] == mime
    assert public.content == (test_settings.upload_dir / "images" / filename).read_bytes()
    question = await api_client.post("/api/v1/questions", headers=headers, json={
        "prompt": "Example with image", "reference_answer": "Example", "max_score": 10, "image_urls": [url],
    })
    assert question.status_code == 201 and question.json()["image_urls"] == [url]


async def test_rejected_uploads_leave_no_files(api_client, auth_headers, test_settings):
    headers = await auth_headers(UserRole.teacher)
    for content, mime, code in [(png(), "image/gif", 415), (png(), "image/webp", 415),
                                (b"<svg/>", "image/svg+xml", 415), (b"fake", "image/png", 400),
                                (png(), "image/jpeg", 400), (b"", "image/png", 400),
                                (b"x" * (5 * 1024 * 1024 + 1), "image/png", 413)]:
        result = await api_client.post("/api/v1/uploads/images", headers=headers, files={"file": ("x.png", content, mime)})
        assert result.status_code == code, result.text
    assert not list(test_settings.upload_dir.rglob("*.*"))


async def test_upload_permissions_and_documented_errors(api_client, auth_headers):
    for headers, code in [({}, 401), (await auth_headers(UserRole.student), 403)]:
        result = await api_client.post("/api/v1/uploads/images", headers=headers, files={"file": ("x.png", png(), "image/png")})
        assert result.status_code == code
    schema = (await api_client.get("/openapi.json")).json()
    responses = schema["paths"]["/api/v1/uploads/images"]["post"]["responses"]
    assert {"200", "400", "401", "403", "413", "415", "422", "503"} <= responses.keys()
