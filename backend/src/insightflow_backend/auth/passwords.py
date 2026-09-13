"""Password hashing via the `bcrypt` package directly, not `passlib` -- passlib is unmaintained
and its bcrypt backend self-test is incompatible with bcrypt>=4.1 (which removed the silent
72-byte-truncation behavior passlib's `detect_wrap_bug` check depends on, and now raises
ValueError instead), so `passlib[bcrypt]` hard-fails on every hash/verify call with a current
bcrypt install. bcrypt itself enforces the 72-byte limit; MAX_PASSWORD_BYTES lets callers (the
auth router's request schemas) reject an over-length password with a clear 422 instead of
bcrypt's own less friendly ValueError.
"""
import bcrypt

MAX_PASSWORD_BYTES = 72


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
