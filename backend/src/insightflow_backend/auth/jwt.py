"""Access tokens are short-lived signed JWTs (stateless, never touch the DB to validate).
Refresh tokens are opaque random strings, stored hashed and rotated on every use -- see
db/models.py's RefreshToken docstring for why (closes the replay window a static refresh token
leaves open). Two different mechanisms on purpose: JWTs can't be revoked without a blacklist,
which is exactly the DB-backed job the refresh token already does, so there's no reason to also
make the long-lived credential a stateless JWT.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt as pyjwt

from insightflow_backend.config import settings


class TokenError(Exception):
    pass


def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_ttl_minutes),
    }
    return pyjwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    try:
        return pyjwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except pyjwt.PyJWTError as e:
        raise TokenError(str(e)) from e


def generate_refresh_token() -> tuple[str, str, datetime]:
    """Returns (raw_token, token_hash, expires_at). The raw token is handed to the client once
    and never stored; only its hash is persisted, same principle as password storage.
    """
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw), datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_ttl_days
    )


def hash_refresh_token(raw: str) -> str:
    # Refresh tokens are already high-entropy random strings (not user-chosen secrets), so a
    # fast cryptographic hash is appropriate here -- unlike passwords, there's no offline
    # brute-force risk to defend against with a slow KDF.
    return hashlib.sha256(raw.encode()).hexdigest()
