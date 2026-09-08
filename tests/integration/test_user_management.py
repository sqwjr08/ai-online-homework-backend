import asyncio

import bcrypt
import jwt
import pytest
from beanie import PydanticObjectId

from app.models import ClassGroup, User, UserRole

pytestmark = pytest.mark.integration


async def login(client, user, password="Test-password-123"):
    return await client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": password,
        },
    )


@pytest.mark.parametrize("role", [UserRole.teacher, UserRole.student])
async def test_disable_enable_revokes_old_session(api_client, accounts, auth_headers, role):
    admin = await auth_headers(UserRole.admin)
    old = await auth_headers(role)
    user = accounts[role]
    path = f"/api/v1/users/{user.id}/status"
    for active in [False, False, True, True]:
        response = await api_client.patch(path, headers=admin, json={"is_active": active})
        assert response.status_code == 200
        assert response.json()["is_active"] is active
        assert "token_version" not in response.json()
        assert (await api_client.get("/api/v1/auth/me", headers=old)).status_code == 401
        assert (await login(api_client, user)).status_code == (200 if active else 403)
    assert (await User.get(user.id)).token_version == 2
    fresh = await auth_headers(role)
    assert (await api_client.get("/api/v1/auth/me", headers=fresh)).status_code == 200
    await api_client.patch(path, headers=admin, json={"is_active": True})
    assert (await api_client.get("/api/v1/auth/me", headers=fresh)).status_code == 200


@pytest.mark.parametrize("role", [UserRole.teacher, UserRole.student])
async def test_password_reset_revokes_all_old_tokens(api_client, accounts, auth_headers, role):
    admin = await auth_headers(UserRole.admin)
    old = await auth_headers(role)
    user = accounts[role]
    new_password = "\u6c49" * 100
    response = await api_client.post(
        f"/api/v1/users/{user.id}/reset-password", headers=admin, json={"password": new_password}
    )
    assert response.status_code == 200
    assert "password_hash" not in response.json() and "token_version" not in response.json()
    assert (await login(api_client, user)).status_code == 401
    assert (await api_client.get("/api/v1/auth/me", headers=old)).status_code == 401
    fresh = await login(api_client, user, new_password)
    assert fresh.status_code == 200
    assert (
        await api_client.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": "Bearer " + fresh.json()["access_token"],
            },
        )
    ).status_code == 200
    saved = await User.get(user.id)
    assert saved.token_version == 1 and saved.password_hash.startswith("$argon2id$")
    assert saved.role == user.role and saved.class_id == user.class_id


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("PATCH", "status", {"is_active": False}),
        ("POST", "reset-password", {"password": "new-password"}),
    ],
)
@pytest.mark.parametrize("actor", [None, UserRole.teacher, UserRole.student, UserRole.admin])
async def test_management_permissions(
    api_client, accounts, auth_headers, method, suffix, body, actor
):
    # Administrators are protected even from themselves; other roles cannot manage anyone.
    target = accounts[UserRole.admin] if actor == UserRole.admin else accounts[UserRole.student]
    before = (await User.get(target.id)).model_dump()
    headers = await auth_headers(actor) if actor else {}
    response = await api_client.request(
        method, f"/api/v1/users/{target.id}/{suffix}", headers=headers, json=body
    )
    assert response.status_code == (401 if actor is None else 403)
    assert (await User.get(target.id)).model_dump() == before


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("PATCH", "status", {"is_active": False}),
        ("POST", "reset-password", {"password": "new-password"}),
    ],
)
@pytest.mark.parametrize("identifier,expected", [("invalid", 422), (str(PydanticObjectId()), 404)])
async def test_invalid_or_missing_account(
    api_client, auth_headers, method, suffix, body, identifier, expected
):
    response = await api_client.request(
        method,
        f"/api/v1/users/{identifier}/{suffix}",
        headers=await auth_headers(UserRole.admin),
        json=body,
    )
    assert response.status_code == expected


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("PATCH", "status", {}),
        ("PATCH", "status", {"is_active": "false"}),
        ("PATCH", "status", {"is_active": 0}),
        ("PATCH", "status", {"is_active": True, "role": "admin"}),
        ("POST", "reset-password", {}),
        ("POST", "reset-password", {"password": "   "}),
        ("POST", "reset-password", {"password": "abc"}),
        ("POST", "reset-password", {"password": "x" * 129}),
        ("POST", "reset-password", {"password": "valid-password", "token_version": 0}),
    ],
)
async def test_invalid_management_payload(api_client, accounts, auth_headers, method, suffix, body):
    target = accounts[UserRole.student]
    response = await api_client.request(
        method,
        f"/api/v1/users/{target.id}/{suffix}",
        headers=await auth_headers(UserRole.admin),
        json=body,
    )
    assert response.status_code == 422
    assert (await User.get(target.id)).token_version == 0


async def test_legacy_document_and_versionless_token(
    api_client, accounts, auth_headers, test_settings
):
    admin = await auth_headers(UserRole.admin)
    user = accounts[UserRole.student]
    await User.get_pymongo_collection().update_one(
        {"_id": user.id}, {"$unset": {"token_version": ""}}
    )
    old = jwt.encode(
        {"sub": str(user.id), "exp": 4102444800}, test_settings.jwt_secret, algorithm="HS256"
    )
    headers = {"Authorization": "Bearer " + old}
    assert (await api_client.get("/api/v1/auth/me", headers=headers)).status_code == 200
    assert (await login(api_client, user)).status_code == 200
    await api_client.post(
        f"/api/v1/users/{user.id}/reset-password", headers=admin, json={"password": "new-password"}
    )
    assert (await User.get(user.id)).token_version == 1
    assert (await api_client.get("/api/v1/auth/me", headers=headers)).status_code == 401


@pytest.mark.parametrize("version", [None, True, "0", -1, 0.0, [], 99])
async def test_invalid_or_wrong_token_version(api_client, accounts, test_settings, version):
    token = jwt.encode(
        {"sub": str(accounts[UserRole.student].id), "exp": 4102444800, "token_version": version},
        test_settings.jwt_secret,
        algorithm="HS256",
    )
    response = await api_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_reset_legacy_long_password_does_not_enable_account(
    api_client, accounts, auth_headers
):
    user = accounts[UserRole.student]
    user.password_hash = bcrypt.hashpw(b"x" * 72, bcrypt.gensalt(rounds=4)).decode()
    user.is_active = False
    await user.save()
    admin = await auth_headers(UserRole.admin)
    assert (
        await api_client.post(
            f"/api/v1/users/{user.id}/reset-password", headers=admin, json={"password": "x" * 100}
        )
    ).status_code == 200
    assert (await login(api_client, user, "x" * 100)).status_code == 403
    await api_client.patch(
        f"/api/v1/users/{user.id}/status", headers=admin, json={"is_active": True}
    )
    assert (await login(api_client, user, "x" * 100)).status_code == 200


@pytest.mark.parametrize("change", ["reset", "disable-enable"])
async def test_change_during_password_verification_rejects_login(
    api_client,
    accounts,
    auth_headers,
    monkeypatch,
    change,
):
    from app.api.routes import auth

    user = accounts[UserRole.student]
    admin = await auth_headers(UserRole.admin)
    original = auth.run_in_threadpool

    async def change_during_verification(function, *args):
        result = await original(function, *args)
        if change == "reset":
            await api_client.post(
                f"/api/v1/users/{user.id}/reset-password",
                headers=admin,
                json={"password": "replacement"},
            )
        else:
            for active in [False, True]:
                await api_client.patch(
                    f"/api/v1/users/{user.id}/status", headers=admin, json={"is_active": active}
                )
        return result

    monkeypatch.setattr(auth, "run_in_threadpool", change_during_verification)
    assert (await login(api_client, user)).status_code == 401


async def test_concurrent_resets_increment_atomically(api_client, accounts, auth_headers):
    user = accounts[UserRole.student]
    admin = await auth_headers(UserRole.admin)
    results = await asyncio.gather(
        *[
            api_client.post(
                f"/api/v1/users/{user.id}/reset-password",
                headers=admin,
                json={"password": password},
            )
            for password in ["password-one", "password-two"]
        ]
    )
    assert all(result.status_code == 200 for result in results)
    assert (await User.get(user.id)).token_version == 2
    codes = [
        (await login(api_client, user, password)).status_code
        for password in ["password-one", "password-two"]
    ]
    assert sorted(codes) == [200, 401]


async def test_join_class_does_not_restore_old_credentials(
    api_client, accounts, auth_headers, monkeypatch
):
    user = accounts[UserRole.student]
    admin = await auth_headers(UserRole.admin)
    student_headers = await auth_headers(UserRole.student)
    group = await ClassGroup(
        name="Class A", code="ABC123", teacher_id=accounts[UserRole.teacher].id
    ).insert()
    collection_type = type(User.get_pymongo_collection())
    original = collection_type.update_one

    async def reset_before_membership_write(self, *args, **kwargs):
        response = await api_client.post(
            f"/api/v1/users/{user.id}/reset-password",
            headers=admin,
            json={"password": "replacement"},
        )
        assert response.status_code == 200
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(collection_type, "update_one", reset_before_membership_write)
    response = await api_client.post(
        "/api/v1/classes/join", headers=student_headers, json={"code": group.code}
    )
    assert response.status_code == 401
    saved = await User.get(user.id)
    assert saved.token_version == 1 and saved.class_id is None
    assert (await login(api_client, user)).status_code == 401
    assert (await login(api_client, user, "replacement")).status_code == 200


async def test_management_openapi(api_client):
    schema = (await api_client.get("/openapi.json")).json()
    for method, suffix in [("patch", "status"), ("post", "reset-password")]:
        operation = schema["paths"][f"/api/v1/users/{{user_id}}/{suffix}"][method]
        assert {"200", "401", "403", "404", "422"}.issubset(operation["responses"])
    assert "token_version" not in schema["components"]["schemas"]["UserRead"]["properties"]
