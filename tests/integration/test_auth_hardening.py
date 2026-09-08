import json

import jwt
import pytest

from app.core.security import create_access_token, hash_password, verify_password
from app.models import User, UserRole

pytestmark = pytest.mark.integration

# Generated using the old Passlib installation before replacing it.
LEGACY_HASH = "$2b$12$nz1gO1Xsvq8zFV3q7T01Au5RVfD1FhcCfHTsRYWTbPwIZKvsL7EDS"


async def test_legacy_login_upgrades_only_hash_once(api_client, accounts, account_password):
    user = accounts[UserRole.student]
    user.password_hash = LEGACY_HASH
    await user.save()
    before = user.model_dump(exclude={"password_hash"})
    for attempt in range(2):
        response = await api_client.post(
            "/api/v1/auth/login",
            json={
                "username": user.username,
                "password": account_password,
            },
        )
        assert response.status_code == 200
        saved = await User.get(user.id)
        assert saved.model_dump(exclude={"password_hash"}) == before
        assert saved.password_hash.startswith("$argon2id$")
        if attempt == 0:
            upgraded = saved.password_hash
        else:
            assert saved.password_hash == upgraded


@pytest.mark.parametrize(
    "disabled,password,expected",
    [
        (False, "wrong-password", 401),
        (True, "Test-password-123", 403),
    ],
)
async def test_failed_legacy_login_never_changes_hash(
    api_client, accounts, disabled, password, expected
):
    user = accounts[UserRole.student]
    user.password_hash = LEGACY_HASH
    user.is_active = not disabled
    await user.save()
    response = await api_client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": password,
        },
    )
    assert response.status_code == expected
    assert (await User.get(user.id)).password_hash == LEGACY_HASH


@pytest.mark.parametrize("hashed", ["broken", "$2b$12$broken", "$argon2id$broken"])
async def test_corrupt_password_hash_returns_401(api_client, accounts, hashed):
    user = accounts[UserRole.student]
    user.password_hash = hashed
    await user.save()
    response = await api_client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": "password",
        },
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "change",
    [
        {"sub": None},
        {"sub": ""},
        {"sub": "not-an-object-id"},
        {"sub": "z" * 24},
        {"sub": 123},
        {"sub": []},
        {"exp": None},
        {"exp": "4102444800"},
        {"exp": []},
        {"exp": True},
        {"exp": float("inf")},
        {"exp": float("nan")},
        {"iat": []},
        {"iat": None},
        {"iat": 4102444800},
        {"nbf": None},
        {"nbf": {}},
        {"nbf": 4102444800},
    ],
)
async def test_invalid_claims_return_401(api_client, accounts, test_settings, change):
    payload = {"sub": str(accounts[UserRole.student].id), "exp": 4102444800, **change}
    token = jwt.encode(payload, test_settings.jwt_secret, algorithm="HS256")
    response = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("missing", ["sub", "exp"])
async def test_required_claims_cannot_be_omitted(api_client, accounts, test_settings, missing):
    payload = {"sub": str(accounts[UserRole.student].id), "exp": 4102444800}
    del payload[missing]
    token = jwt.encode(payload, test_settings.jwt_secret, algorithm="HS256")
    response = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "variant", ["unsigned", "wrong-algorithm", "wrong-key", "tampered", "array"]
)
async def test_invalid_signatures_and_payloads(api_client, accounts, test_settings, variant):
    payload = {"sub": str(accounts[UserRole.student].id), "exp": 4102444800}
    key = test_settings.jwt_secret
    if variant == "unsigned":
        token = jwt.encode(payload, "", algorithm="none")
    elif variant == "wrong-algorithm":
        token = jwt.encode(payload, key, algorithm="HS512")
    elif variant == "wrong-key":
        token = jwt.encode(payload, "wrong-key-" * 8, algorithm="HS256")
    elif variant == "array":
        token = jwt.api_jws.encode(json.dumps([payload]).encode(), key, algorithm="HS256")
    else:
        token = jwt.encode(payload, key, algorithm="HS256")
        header, body, signature = token.split(".")
        token = f"{header}.{body}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
    response = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_database_role_overrides_claimed_role(api_client, accounts):
    token = create_access_token(str(accounts[UserRole.student].id), {"role": "admin"})
    response = await api_client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


@pytest.mark.parametrize("password", ["a" * 128, "\u6c49" * 128])
async def test_register_and_login_long_password(api_client, password):
    response = await api_client.post(
        "/api/v1/auth/register",
        json={
            "username": "new_student",
            "password": password,
            "role": "student",
        },
    )
    assert response.status_code == 201
    user = await User.find_one(User.username == "new_student")
    assert user.password_hash.startswith("$argon2id$")
    for candidate, expected in [(password, 200), (password[:-1] + "X", 401)]:
        response = await api_client.post(
            "/api/v1/auth/login",
            json={
                "username": user.username,
                "password": candidate,
            },
        )
        assert response.status_code == expected


async def test_admin_created_password_uses_argon2(api_client, auth_headers):
    response = await api_client.post(
        "/api/v1/users",
        headers=await auth_headers(UserRole.admin),
        json={"username": "new_teacher", "password": "a" * 100, "role": "teacher"},
    )
    assert response.status_code == 201
    user = await User.find_one(User.username == "new_teacher")
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password("a" * 100, user.password_hash)


async def test_rehash_does_not_overwrite_concurrent_reset(api_client, accounts, monkeypatch):
    from app.api.routes import auth

    user = accounts[UserRole.student]
    user.password_hash = LEGACY_HASH
    await user.save()
    replacement = hash_password("replacement-password")
    real_run = auth.run_in_threadpool

    async def reset_during_verification(function, *args):
        result = await real_run(function, *args)
        await User.find_one(User.id == user.id).update({"$set": {"password_hash": replacement}})
        return result

    monkeypatch.setattr(auth, "run_in_threadpool", reset_during_verification)
    response = await api_client.post(
        "/api/v1/auth/login",
        json={
            "username": user.username,
            "password": "Test-password-123",
        },
    )
    assert response.status_code == 401
    assert (await User.get(user.id)).password_hash == replacement
