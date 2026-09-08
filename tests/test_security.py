from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_round_trip():
    password_hash = hash_password("admin")

    assert verify_password("admin", password_hash)
    assert not verify_password("wrong", password_hash)


def test_access_token_round_trip(test_settings):
    token = create_access_token("507f1f77bcf86cd799439011", {"role": "admin"})

    payload = decode_access_token(token)

    assert payload["sub"] == "507f1f77bcf86cd799439011"
    assert payload["role"] == "admin"
