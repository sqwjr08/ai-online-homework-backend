from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from bson import ObjectId
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import get_settings

password_hasher = PasswordHash((Argon2Hasher(), BcryptHasher()))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        if BcryptHasher.identify(password_hash) and len(password.encode("utf-8")) > 72:
            return False
        return password_hasher.verify(password, password_hash)
    except (UnknownHashError, ValueError, TypeError):
        return False


def verify_and_update_password(password: str, password_hash: str) -> tuple[bool, str | None]:
    try:
        # Legacy bcrypt cannot prove any characters beyond the first 72 bytes.
        if BcryptHasher.identify(password_hash) and len(password.encode("utf-8")) > 72:
            return False, None
        return password_hasher.verify_and_update(password, password_hash)
    except (UnknownHashError, ValueError, TypeError):
        return False, None


def create_access_token(
    subject: str, claims: dict[str, Any] | None = None, *, token_version: int = 0
) -> str:
    if not isinstance(subject, str) or not ObjectId.is_valid(subject):
        raise ValueError("Invalid token subject")
    if type(token_version) is not int or token_version < 0:
        raise ValueError("Invalid token version")
    if claims and {"sub", "exp", "iat", "nbf", "token_version"}.intersection(claims):
        raise ValueError("Reserved token claims cannot be overridden")
    settings = get_settings()
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict[str, Any] = {
        **(claims or {}),
        "sub": subject,
        "exp": expires_at,
        "iat": issued_at,
        "token_version": token_version,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        if not isinstance(payload["sub"], str) or not ObjectId.is_valid(payload["sub"]):
            raise ValueError("Invalid token subject")
        for claim in ("exp", "iat", "nbf"):
            if claim in payload and type(payload[claim]) is not int:
                raise ValueError("Token timestamps must be integers")
        version = payload.get("token_version", 0)
        if type(version) is not int or version < 0:
            raise ValueError("Invalid token version")
        return payload
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid token") from exc
