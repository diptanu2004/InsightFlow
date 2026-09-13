from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from insightflow_backend.auth.jwt import create_access_token, generate_refresh_token, hash_refresh_token
from insightflow_backend.auth.passwords import MAX_PASSWORD_BYTES, hash_password, verify_password
from insightflow_backend.db.models import RefreshToken, User
from insightflow_backend.db.session import get_db

router = APIRouter()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def _password_within_bcrypt_limit(cls, v: str) -> str:
        # bcrypt hard-limits input to 72 bytes; reject clearly here instead of letting bcrypt
        # raise ValueError deep inside hash_password().
        if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _issue_token_pair(db: Session, user: User) -> TokenPair:
    raw_refresh, refresh_hash, expires_at = generate_refresh_token()
    db.add(RefreshToken(user_id=user.id, token_hash=refresh_hash, expires_at=expires_at))
    db.commit()
    access_token = create_access_token(user_id=str(user.id), email=user.email)
    return TokenPair(access_token=access_token, refresh_token=raw_refresh)


@router.post("/register", response_model=TokenPair, status_code=201)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> TokenPair:
    # No email-verification flow yet (deliberately out of scope for Phase 6 -- see CLAUDE.md /
    # the Phase 6 plan doc), so registering issues real tokens immediately, same as login.
    if db.query(User).filter(User.email == body.email).first() is not None:
        raise HTTPException(status_code=409, detail="email already registered")

    user = User(email=body.email, hashed_password=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return _issue_token_pair(db, user)


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
    user = db.query(User).filter(User.email == body.email).first()
    if user is None or not user.is_active or not verify_password(body.password, user.hashed_password):
        # Same message for "no such user" and "wrong password" -- doesn't leak which one it was.
        raise HTTPException(status_code=401, detail="invalid email or password")
    return _issue_token_pair(db, user)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> TokenPair:
    token_hash = hash_refresh_token(body.refresh_token)
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()

    now = datetime.now(timezone.utc)
    if stored is None or stored.revoked_at is not None or stored.expires_at < now:
        raise HTTPException(status_code=401, detail="invalid, expired, or revoked refresh token")

    # Rotate on use: revoke the presented token so it can never be replayed, even if it leaks
    # after this point -- see db/models.py's RefreshToken docstring.
    stored.revoked_at = now
    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        db.commit()
        raise HTTPException(status_code=401, detail="user not found or inactive")

    return _issue_token_pair(db, user)


@router.post("/logout", status_code=204)
def logout(body: LogoutRequest, db: Session = Depends(get_db)) -> None:
    token_hash = hash_refresh_token(body.refresh_token)
    stored = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(timezone.utc)
        db.commit()
    # Logout is idempotent -- an already-revoked or unknown token still returns 204, same as
    # calling logout twice should be harmless rather than a client-visible error.
