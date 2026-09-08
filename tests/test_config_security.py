from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "overrides",
    [
        {"environment": "prod"},
        {"environment": "staging"},
        {"jwt_algorithm": "none"},
        {"jwt_algorithm": "HS512"},
        {"jwt_expire_minutes": 0},
        {"jwt_expire_minutes": -1},
        {"jwt_expire_minutes": 43201},
        {"jwt_secret": ""},
        {"jwt_secret": "   "},
        {"environment": "test", "enable_dev_default_admin": True},
        {"environment": "production", "enable_dev_default_admin": True},
        {"environment": "production"},
        {"environment": "production", "jwt_secret": "a" * 31},
        {"environment": "production", "jwt_secret": " " * 32},
        {"environment": "production", "jwt_secret": "change-me-before-production      "},
    ],
)
def test_unsafe_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


@pytest.mark.parametrize("minutes", [1, 1440, 43200])
def test_valid_production_configuration(minutes):
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="a" * 64,
        jwt_expire_minutes=minutes,
    )
    assert settings.enable_dev_default_admin is False
    assert settings.jwt_algorithm == "HS256"


def test_development_admin_remains_opt_in():
    assert Settings(_env_file=None).enable_dev_default_admin is False
    assert Settings(_env_file=None, enable_dev_default_admin=True).enable_dev_default_admin is True


@pytest.mark.parametrize(
    "origin",
    [
        "*", "null", "ftp://example.com", "https://*.example.com",
        "https://user:password@example.com", "https://example.com/api",
        "https://example.com?query=1", "https://example.com#fragment",
    ],
)
def test_invalid_cors_origins_are_rejected(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


def test_cors_json_environment_is_normalized(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS", '["https://EXAMPLE.com:443/","https://example.com","http://localhost:5173"]'
    )
    assert Settings(_env_file=None).cors_origins == [
        "https://example.com", "http://localhost:5173",
    ]


def test_empty_cors_list_is_allowed():
    assert Settings(_env_file=None, cors_origins=[]).cors_origins == []


def test_secrets_are_hidden_in_repr_and_validation_errors():
    secret = "sensitive-secret"
    settings = Settings(_env_file=None, jwt_secret=secret, dev_admin_password=secret)
    assert secret not in repr(settings)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, environment="production", jwt_secret=secret)
    assert secret not in str(error.value)


def test_bad_production_config_rejected_before_database_initialization(monkeypatch):
    from app import main

    initialize = AsyncMock()
    monkeypatch.setattr(main, "init_database", initialize)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "change-me-before-production")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        main.create_app()
    initialize.assert_not_called()


async def test_production_never_bootstraps_development_admin(monkeypatch):
    from app.db import ensure_dev_admin
    from app.models import User

    lookup = AsyncMock()
    monkeypatch.setattr(User, "find_one", lookup)
    settings = Settings(_env_file=None, environment="production", jwt_secret="a" * 64)
    # Even a caller bypassing configuration validation cannot enable this bootstrap.
    settings.enable_dev_default_admin = True
    await ensure_dev_admin(settings)
    lookup.assert_not_called()
