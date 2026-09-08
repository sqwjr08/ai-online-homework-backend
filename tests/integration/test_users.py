import asyncio
from datetime import UTC, datetime

import pytest

from app.models import User, UserRole

pytestmark = pytest.mark.integration


async def test_registration_defaults_to_active_student(api_client):
    response = await api_client.post(
        "/api/v1/auth/register",
        json={
            "username": "new_student",
            "password": "test-password",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "student"
    assert response.json()["is_active"] is True
    assert response.json()["class_id"] is None
    assert "password_hash" not in response.json()
    response = await api_client.post(
        "/api/v1/auth/login",
        json={
            "username": "new_student",
            "password": "test-password",
        },
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        {"role": "admin"},
        {"role": "teacher"},
        {"is_active": False},
        {"class_id": "507f1f77bcf86cd799439011"},
        {"password_hash": "injected"},
    ],
)
async def test_registration_rejects_privileged_fields(api_client, extra):
    response = await api_client.post(
        "/api/v1/auth/register",
        json={
            "username": "new_student",
            "password": "test-password",
            **extra,
        },
    )
    assert response.status_code == 422
    assert await User.count() == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("username", "   "),
        ("username", " student"),
        ("username", "stu dent"),
        ("username", "stu\u200bdent"),
        ("username", "ab"),
        ("username", "a" * 51),
        ("password", "    "),
        ("password", "abc"),
        ("password", "a" * 129),
    ],
)
async def test_invalid_account_input(api_client, field, value):
    response = await api_client.post(
        "/api/v1/auth/register",
        json={
            "username": "new_student",
            "password": "test-password",
            field: value,
        },
    )
    assert response.status_code == 422
    assert await User.count() == 0


async def test_password_whitespace_is_preserved(api_client):
    credentials = {"username": "\u5b66\u751f\u7532", "password": " pass "}
    assert (await api_client.post("/api/v1/auth/register", json=credentials)).status_code == 201
    assert (await api_client.post("/api/v1/auth/login", json=credentials)).status_code == 200
    credentials["password"] = "pass"
    assert (await api_client.post("/api/v1/auth/login", json=credentials)).status_code == 401


@pytest.mark.parametrize("actor", [UserRole.teacher, UserRole.student])
async def test_non_admin_cannot_create_accounts(api_client, auth_headers, actor):
    response = await api_client.post(
        "/api/v1/users",
        headers=await auth_headers(actor),
        json={
            "username": "new_student",
            "password": "test-password",
            "role": "student",
        },
    )
    assert response.status_code == 403
    assert await User.find_one(User.username == "new_student") is None


async def test_admin_cannot_create_admin(api_client, auth_headers):
    response = await api_client.post(
        "/api/v1/users",
        headers=await auth_headers(UserRole.admin),
        json={"username": "new_admin", "password": "test-password", "role": "admin"},
    )
    assert response.status_code == 400


@pytest.mark.parametrize("active", [True, False])
async def test_admin_creates_student_with_requested_state(api_client, auth_headers, active):
    credentials = {"username": "new_student", "password": "test-password"}
    response = await api_client.post(
        "/api/v1/users",
        headers=await auth_headers(UserRole.admin),
        json={**credentials, "role": "student", "is_active": active},
    )
    assert response.status_code == 201
    assert response.json()["is_active"] is active
    assert (await api_client.post("/api/v1/auth/login", json=credentials)).status_code == (
        200 if active else 403
    )


async def test_duplicate_username_across_creation_endpoints(api_client, auth_headers):
    headers = await auth_headers(UserRole.admin)
    credentials = {"username": "same_student", "password": "test-password", "role": "student"}
    assert (
        await api_client.post("/api/v1/users", headers=headers, json=credentials)
    ).status_code == 201
    assert (await api_client.post("/api/v1/auth/register", json=credentials)).status_code == 409
    assert (
        await api_client.post("/api/v1/users", headers=headers, json=credentials)
    ).status_code == 409
    assert await User.find(User.username == "same_student").count() == 1


async def test_concurrent_creation_unique_index_conflict_is_409(
    api_client, auth_headers, monkeypatch
):
    headers = await auth_headers(UserRole.admin)
    original_insert = User.insert
    ready = asyncio.Event()
    arrivals = 0

    async def synchronized_insert(self, *args, **kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=5)
        return await original_insert(self, *args, **kwargs)

    monkeypatch.setattr(User, "insert", synchronized_insert)
    data = {"username": "concurrent_student", "password": "test-password", "role": "student"}
    results = await asyncio.gather(
        api_client.post("/api/v1/auth/register", json=data),
        api_client.post("/api/v1/users", headers=headers, json=data),
    )
    assert sorted(response.status_code for response in results) == [201, 409]
    assert await User.find(User.username == data["username"]).count() == 1


async def test_user_pagination_is_stable(api_client, accounts, auth_headers):
    headers = await auth_headers(UserRole.admin)
    for user in accounts.values():
        user.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        await user.save()
    ids = []
    for page in range(1, 4):
        response = await api_client.get(
            "/api/v1/users", headers=headers, params={"page": page, "page_size": 1}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3 and body["page"] == page and body["page_size"] == 1
        assert len(body["items"]) == 1
        assert "password_hash" not in body["items"][0]
        ids.append(body["items"][0]["id"])
    assert ids == sorted([str(user.id) for user in accounts.values()], reverse=True)
    response = await api_client.get(
        "/api/v1/users", headers=headers, params={"page": 4, "page_size": 1}
    )
    assert response.json()["items"] == [] and response.json()["total"] == 3


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"role": "student"}, ["test_student"]),
        ({"is_active": False}, ["test_student"]),
        ({"is_active": True}, ["test_admin", "test_teacher"]),
        ({"username": "STU"}, ["test_student"]),
        ({"role": "teacher", "is_active": False}, []),
        ({"username": ".*"}, []),
        ({"username": "nobody"}, []),
    ],
)
async def test_user_filters(api_client, accounts, auth_headers, params, expected):
    headers = await auth_headers(UserRole.admin)
    student = accounts[UserRole.student]
    student.is_active = False
    await student.save()
    response = await api_client.get("/api/v1/users", headers=headers, params=params)
    assert response.status_code == 200
    body = response.json()
    assert sorted(user["username"] for user in body["items"]) == expected
    assert body["total"] == len(expected)
    assert body["page"] == 1 and body["page_size"] == 20


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": 1000001},
        {"page": "abc"},
        {"page_size": 0},
        {"page_size": 101},
        {"role": "owner"},
        {"is_active": "wrong"},
        {"username": ""},
        {"username": "a" * 51},
    ],
)
async def test_invalid_query_parameters(api_client, auth_headers, params):
    response = await api_client.get(
        "/api/v1/users", headers=await auth_headers(UserRole.admin), params=params
    )
    assert response.status_code == 422


async def test_account_openapi_documents_contract(api_client):
    schema = (await api_client.get("/openapi.json")).json()
    register = schema["components"]["schemas"]["StudentRegister"]
    assert set(register["required"]) == {"username", "password"}
    assert register["additionalProperties"] is False
    operation = schema["paths"]["/api/v1/users"]["get"]
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "page",
        "page_size",
        "role",
        "is_active",
        "username",
    }
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/UserPage"
    )
    assert {"401", "403", "422"}.issubset(operation["responses"])
    assert "409" in schema["paths"]["/api/v1/auth/register"]["post"]["responses"]
