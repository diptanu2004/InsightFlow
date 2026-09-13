import pytest

from insightflow_backend.auth.jwt import (
    TokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)


def test_access_token_round_trips_claims():
    token = create_access_token(user_id="abc-123", email="a@example.com")
    payload = decode_access_token(token)
    assert payload["sub"] == "abc-123"
    assert payload["email"] == "a@example.com"


def test_decode_rejects_garbage_token():
    with pytest.raises(TokenError):
        decode_access_token("not.a.jwt")


def test_decode_rejects_tampered_token():
    token = create_access_token(user_id="abc-123", email="a@example.com")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(TokenError):
        decode_access_token(tampered)


def test_refresh_token_hash_is_deterministic_and_not_the_raw_value():
    raw, token_hash, expires_at = generate_refresh_token()
    assert hash_refresh_token(raw) == token_hash
    assert token_hash != raw


def test_refresh_tokens_are_unique():
    raw1, _, _ = generate_refresh_token()
    raw2, _, _ = generate_refresh_token()
    assert raw1 != raw2
