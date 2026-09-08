from datetime import UTC, datetime, timedelta

import pytest
import jwt
from beanie.odm.fields import PydanticObjectId

from app.core.security import create_access_token
from app.models import UserRole

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", list(UserRole))
async def test_login_and_me_for_each_role(api_client, accounts, account_password, role):
    account = accounts[role]
    response = await api_client.post(
        "/api/v1/auth/login",
        json={"username": account.username, "password": account_password},
    )
    assert response.status_code == 200
    token = response.json()
    assert token["token_type"] == "bearer"
    assert token["access_token"]

    response = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"}
    )
    assert response.status_code == 200
    profile = response.json()
    assert profile["id"] == str(account.id)
    assert profile["username"] == account.username
    assert profile["role"] == role.value
    assert profile["class_id"] is None
    assert "password" not in profile
    assert "password_hash" not in profile


@pytest.mark.parametrize(
    "username,password",
    [
        ("test_student", "wrong-password"),
        ("unknown_student", "Test-password-123"),
    ],
)
async def test_login_rejects_wrong_credentials(api_client, accounts, username, password):
    response = await api_client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 401
    assert "access_token" not in response.json()


async def test_login_requires_password(api_client):
    response = await api_client.post("/api/v1/auth/login", json={"username": "test_student"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/api/v1/auth/me", None),
        ("GET", "/api/v1/users", None),
        ("GET", "/api/v1/classes/my", None),
        ("GET", "/api/v1/questions", None),
        ("GET", "/api/v1/assignments/my", None),
        ("GET", "/api/v1/assignments/000000000000000000000000", None),
        ("GET", "/api/v1/submissions/000000000000000000000000", None),
        ("POST", "/api/v1/classes", {"name": "Test class"}),
        ("POST", "/api/v1/classes/join", {"code": "ABC123"}),
        (
            "POST",
            "/api/v1/users",
            {
                "username": "new_student",
                "password": "Test-password-123",
                "role": "student",
            },
        ),
    ],
)
async def test_protected_routes_require_login(api_client, method, path, payload):
    response = await api_client.request(method, path, json=payload)
    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated"


@pytest.mark.parametrize("authorization", ["Bearer invalid.token.value", "Basic invalid"])
async def test_me_rejects_invalid_authentication(api_client, authorization):
    response = await api_client.get("/api/v1/auth/me", headers={"Authorization": authorization})
    assert response.status_code == 401


async def test_me_rejects_expired_token(api_client, accounts, test_settings):
    token = jwt.encode(
        {
            "sub": str(accounts[UserRole.student].id),
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        test_settings.jwt_secret,
        algorithm="HS256",
    )
    response = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_me_rejects_nonexistent_user(api_client):
    token = create_access_token(str(PydanticObjectId()))
    response = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_disabled_account_cannot_login(api_client, accounts, account_password):
    account = accounts[UserRole.student]
    account.is_active = False
    await account.save()
    response = await api_client.post(
        "/api/v1/auth/login",
        json={"username": account.username, "password": account_password},
    )
    assert response.status_code == 403
    assert "access_token" not in response.json()


async def test_disabling_account_invalidates_existing_access(api_client, accounts, auth_headers):
    headers = await auth_headers(UserRole.student)
    account = accounts[UserRole.student]
    account.is_active = False
    await account.save()
    response = await api_client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.admin, 200),
        (UserRole.teacher, 403),
        (UserRole.student, 403),
    ],
)
async def test_only_admin_can_list_users(api_client, accounts, auth_headers, role, expected_status):
    response = await api_client.get("/api/v1/users", headers=await auth_headers(role))
    assert response.status_code == expected_status
    if expected_status == 200:
        assert {item["username"] for item in response.json()["items"]} == {
            account.username for account in accounts.values()
        }
        assert all("password_hash" not in item for item in response.json()["items"])


@pytest.mark.parametrize(
    "role,expected_status",
    [
        (UserRole.admin, 200),
        (UserRole.teacher, 200),
        (UserRole.student, 403),
    ],
)
async def test_question_bank_role_access(api_client, auth_headers, role, expected_status):
    response = await api_client.get("/api/v1/questions", headers=await auth_headers(role))
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json() == []
