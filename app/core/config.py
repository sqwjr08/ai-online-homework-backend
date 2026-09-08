from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Answer Platform API"
    environment: Literal["development", "test", "production"] = "development"
    mongodb_uri: str = Field(default="mongodb://127.0.0.1:27017", repr=False)
    database_name: str = "answer_platform_dev"
    jwt_secret: str = Field(default="change-me-before-production", min_length=1, repr=False)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = Field(default=60 * 24, ge=1, le=60 * 24 * 30)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "https://localhost:5173"]
    )
    enable_dev_default_admin: bool = False
    dev_admin_username: str = "admin"
    dev_admin_password: str = Field(default="admin", repr=False)
    upload_dir: Path = Path("uploads")
    public_base_url: str = "http://localhost:8000"
    ai_provider: str = "placeholder"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        adapter = TypeAdapter(AnyHttpUrl)
        origins = []
        for origin in value:
            url = adapter.validate_python(origin)
            if (
                url.username is not None
                or url.password is not None
                or url.path not in (None, "/")
                or url.query is not None
                or url.fragment is not None
                or "*" in (url.host or "")
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without credentials")
            normalized = str(url).rstrip("/")
            if normalized not in origins:
                origins.append(normalized)
        return origins

    @model_validator(mode="after")
    def validate_environment_security(self) -> "Settings":
        if self.enable_dev_default_admin and self.environment != "development":
            raise ValueError("The default administrator is allowed only in development")
        if not self.jwt_secret.strip():
            raise ValueError("JWT_SECRET must not be blank")
        if self.environment == "production":
            if (
                self.jwt_secret.strip() == "change-me-before-production"
                or len(self.jwt_secret.strip().encode("utf-8")) < 32
            ):
                raise ValueError("Production requires a non-placeholder JWT_SECRET of at least 32 bytes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
