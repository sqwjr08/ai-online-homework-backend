import bcrypt
import jwt
import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_and_update_password,
    verify_password,
)

SUBJECT = "507f1f77bcf86cd799439011"


@pytest.mark.parametrize(
    "password", ["a" * 128, "\u6c49" * 128, "a" * 72 + "tail", "test\x00password"]
)
def test_new_passwords_verify_all_characters(password):
    hashed = hash_password(password)
    assert hashed.startswith("$argon2id$")
    assert verify_password(password, hashed)
    assert not verify_password(password[:-1] + "X", hashed)
    assert verify_and_update_password(password, hashed) == (True, None)


@pytest.mark.parametrize(
    "hashed",
    [
        "",
        "unknown",
        "$2b$12$broken",
        "$argon2id$broken",
        "$2b$99$" + "a" * 53,
        "$argon2id$v=19$m=1,t=1,p=1$bad$bad",
    ],
)
def test_malformed_hashes_fail_closed(hashed):
    assert not verify_password("password", hashed)
    assert verify_and_update_password("password", hashed) == (False, None)


@pytest.mark.parametrize("password", ["a" * 72 + "suffix", "\u6c49" * 25])
def test_legacy_long_password_is_not_silently_truncated(password):
    hashed = bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=4)).decode()
    assert not verify_password(password, hashed)
    assert verify_and_update_password(password, hashed) == (False, None)


@pytest.mark.parametrize("password", ["a" * 72, "\u6c49" * 24])
def test_legacy_72_byte_boundary_upgrades(password):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    valid, updated = verify_and_update_password(password, hashed)
    assert valid and updated.startswith("$argon2id$")
    assert verify_password(password, updated)


@pytest.mark.parametrize("claim", ["sub", "exp", "iat", "nbf", "token_version"])
def test_reserved_claims_cannot_override_token_policy(test_settings, claim):
    with pytest.raises(ValueError):
        create_access_token(SUBJECT, {claim: 0})


@pytest.mark.parametrize("version", [True, -1, "0", None, 0.5])
def test_invalid_signing_version_is_rejected(test_settings, version):
    with pytest.raises(ValueError):
        create_access_token(SUBJECT, token_version=version)


@pytest.mark.parametrize("subject", ["", "user-id", "z" * 24, 1, None])
def test_token_signing_requires_valid_subject(subject):
    with pytest.raises(ValueError):
        create_access_token(subject)


def test_token_lifetime_comes_from_configuration(test_settings):
    payload = decode_access_token(create_access_token(SUBJECT))
    assert payload["exp"] - payload["iat"] == test_settings.jwt_expire_minutes * 60


def test_token_from_previous_jose_library_is_accepted(test_settings):
    test_settings.jwt_secret = "test-signing-key-for-migration-1234567890"
    # Generated with the previous python-jose installation, no iat claim.
    token = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiI1MDdmMWY3N2JjZjg2Y2Q3OTk0MzkwMTEiLCJleHAiOjQxMDI0NDQ4MDAs"
        "InJvbGUiOiJzdHVkZW50In0.90veoelit8wXVk-SrOzxoPOZGFDvKXWzM2rxQ87Wsj4"
    )
    assert decode_access_token(token)["sub"] == SUBJECT


def test_old_argon2_parameters_are_upgraded():
    from pwdlib.hashers.argon2 import Argon2Hasher

    old = Argon2Hasher(time_cost=1, memory_cost=8192, parallelism=1).hash("password")
    valid, updated = verify_and_update_password("password", old)
    assert valid and updated != old
    assert verify_password("password", updated)


def test_wrong_signature_is_rejected(test_settings):
    token = jwt.encode({"sub": SUBJECT, "exp": 4102444800}, "wrong-key-" * 8, algorithm="HS256")
    with pytest.raises(ValueError, match="Invalid token"):
        decode_access_token(token)
