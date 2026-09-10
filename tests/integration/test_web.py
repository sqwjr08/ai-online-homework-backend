from io import BytesIO
from urllib.parse import urlsplit

import pytest
from PIL import Image

from app.models import UserRole

pytestmark = pytest.mark.integration


async def test_docs_and_openapi_remain_available(api_client):
    docs = await api_client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text
    response = await api_client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    for path in (
        "/api/v1/auth/login", "/api/v1/users", "/api/v1/classes",
        "/api/v1/questions", "/api/v1/assignments", "/api/v1/uploads/images",
    ):
        operation = schema["paths"][path]["post"]
        assert operation["requestBody"]["content"]
        assert "422" in operation["responses"]
    assert "multipart/form-data" in schema["paths"]["/api/v1/uploads/images"]["post"][
        "requestBody"
    ]["content"]
    assert schema["paths"]["/api/v1/auth/me"]["get"]["security"] == [{"HTTPBearer": []}]


async def test_allowed_cors_preflight(api_client, test_settings):
    origin = test_settings.cors_origins[0]
    response = await api_client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"]


async def test_unlisted_cors_origin_is_not_allowed(api_client):
    response = await api_client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://unlisted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    response = await api_client.get("/health", headers={"Origin": "https://unlisted.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


async def test_cors_headers_on_auth_error(api_client, test_settings):
    origin = test_settings.cors_origins[0]
    response = await api_client.get("/api/v1/auth/me", headers={"Origin": origin})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == origin


async def test_request_without_origin_remains_supported(api_client):
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "access-control-allow-origin" not in response.headers


async def test_multipart_image_upload_and_static_read(api_client, auth_headers, test_settings):
    output = BytesIO()
    with Image.new("RGB", (2, 2), "red") as source:
        source.save(output, format="PNG")
    content = output.getvalue()
    response = await api_client.post(
        "/api/v1/uploads/images",
        headers=await auth_headers(UserRole.teacher),
        files={"file": ("test.png", content, "image/png")},
    )
    assert response.status_code == 200
    url = response.json()["url"]
    assert url.startswith(f"{test_settings.public_base_url}/uploads/images/")
    filename = urlsplit(url).path.rsplit("/", 1)[1]
    stored = (test_settings.upload_dir / "images" / filename).read_bytes()
    image = await api_client.get(url)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == stored
    with Image.open(BytesIO(stored)) as decoded:
        assert decoded.size == (2, 2) and decoded.getpixel((0, 0)) == (255, 0, 0, 255)
