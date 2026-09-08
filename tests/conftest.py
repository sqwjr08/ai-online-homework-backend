import os
import re
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from app.models import User, UserRole


@pytest.fixture
def test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Settings]:
    mongodb_uri = os.environ.get("TEST_MONGODB_URI", "mongodb://127.0.0.1:27017")
    # A temporary cwd excludes the developer's .env; clear inherited application settings too.
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    overrides = {
        "ENVIRONMENT": "test",
        "MONGODB_URI": mongodb_uri,
        "DATABASE_NAME": f"answer_platform_test_{uuid4().hex}",
        "JWT_SECRET": uuid4().hex + uuid4().hex,
        "ENABLE_DEV_DEFAULT_ADMIN": "false",
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "PUBLIC_BASE_URL": "http://testserver",
        "AI_PROVIDER": "placeholder",
    }
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def test_app(test_settings: Settings) -> AsyncIterator[FastAPI]:
    database_name = test_settings.database_name
    if test_settings.environment != "test" or not re.fullmatch(
        r"answer_platform_test_[0-9a-f]{32}", database_name
    ):
        raise RuntimeError("Refusing to use a non-test database")

    cleanup_client = AsyncMongoClient(test_settings.mongodb_uri, serverSelectionTimeoutMS=2000)
    owned = False
    owner_token = uuid4().hex
    try:
        try:
            await cleanup_client.admin.command("ping")
        except PyMongoError:
            pytest.fail(
                "Integration tests require MongoDB. Start the local server or set TEST_MONGODB_URI; "
                "use pytest -m 'not integration' to run unit tests only.",
                pytrace=False,
            )
        if database_name in await cleanup_client.list_database_names():
            raise RuntimeError("Refusing to reuse an existing test database")
        ownership = await cleanup_client[database_name].create_collection("_pytest_owner")
        await ownership.insert_one({"_id": owner_token})
        owned = True

        # Import after isolation is active because app.main also exports the default ASGI app.
        from app.main import create_app

        application = create_app()
        async with application.router.lifespan_context(application):
            yield application
    finally:
        try:
            if owned:
                marker = await cleanup_client[database_name]["_pytest_owner"].find_one(
                    {"_id": owner_token}
                )
                if marker is None:
                    raise RuntimeError("Test database ownership changed; refusing cleanup")
                await cleanup_client.drop_database(database_name)
        finally:
            await cleanup_client.close()


@pytest_asyncio.fixture
async def api_client(test_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as client:
        yield client


@pytest.fixture(scope="session")
def account_password() -> str:
    return "Test-password-123"


@pytest.fixture(scope="session")
def account_password_hash(account_password: str) -> str:
    return hash_password(account_password)


@pytest_asyncio.fixture
async def accounts(test_app: FastAPI, account_password_hash: str) -> dict[UserRole, User]:
    users = {}
    for role in UserRole:
        user = User(
            username=f"test_{role.value}",
            password_hash=account_password_hash,
            role=role,
        )
        await user.insert()
        users[role] = user
    return users


@pytest.fixture
def auth_headers(
    api_client: AsyncClient, accounts: dict[UserRole, User], account_password: str
) -> Callable:
    async def login_as(role: UserRole) -> dict[str, str]:
        response = await api_client.post(
            "/api/v1/auth/login",
            json={"username": accounts[role].username, "password": account_password},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return login_as
