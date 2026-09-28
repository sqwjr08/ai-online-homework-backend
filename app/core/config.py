from functools import lru_cache
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, TypeAdapter, field_validator, model_validator
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
    ai_job_timeout_seconds: float = Field(default=60, gt=0, le=3600)
    ai_lease_seconds: int = Field(default=90, ge=1, le=7200)
    ai_poll_seconds: float = Field(default=2, gt=0, le=60)
    ai_max_attempts: int = Field(default=3, ge=1, le=10)
    ai_retry_base_seconds: int = Field(default=5, ge=1, le=300)
    bailian_api_key: SecretStr | None = Field(default=None, repr=False)
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    bailian_model: str = Field(default="qwen3.8-flash", min_length=1, max_length=128)
    bailian_request_timeout_seconds: float = Field(default=30, gt=0, le=300)
    bailian_max_output_tokens: int = Field(default=512, ge=64, le=4096)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @field_validator("bailian_api_key", mode="before")
    @classmethod
    def normalize_bailian_key(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("bailian_model")
    @classmethod
    def validate_bailian_model(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
            raise ValueError("Invalid Bailian model ID")
        return value

    @field_validator("bailian_base_url")
    @classmethod
    def validate_bailian_url(cls, value: str) -> str:
        url = urlsplit(value)
        host = url.hostname or ""
        allowed = host == "dashscope.aliyuncs.com" or re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.cn-beijing\.maas\.aliyuncs\.com", host,
        )
        if (url.scheme != "https" or not allowed or url.username is not None
                or url.password is not None or url.port not in (None, 443)
                or url.path.rstrip("/") != "/compatible-mode/v1" or url.query or url.fragment):
            raise ValueError("Bailian requires an official Beijing HTTPS base URL")
        return value.rstrip("/")

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
        if self.ai_provider == "bailian":
            if self.bailian_api_key is None or not self.bailian_api_key.get_secret_value().strip():
                raise ValueError("BAILIAN_API_KEY is required for the Bailian provider")
            if self.bailian_request_timeout_seconds >= self.ai_job_timeout_seconds:
                raise ValueError("Bailian request timeout must be shorter than the whole-job timeout")
        if self.ai_lease_seconds <= self.ai_job_timeout_seconds:
            raise ValueError("AI lease must exceed the whole-job timeout")
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
