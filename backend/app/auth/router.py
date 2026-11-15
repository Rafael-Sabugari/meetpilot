import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import DB, CurrentUser, tokens
from app.auth.ratelimit import RateLimiter
from app.auth.security import TokenService, hash_password, verify_password
from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import RefreshToken, User

router = APIRouter(tags=["auth"])
_login_limiter = RateLimiter(max_events=10, window_seconds=300)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth2 scheme name, not a secret)
    expires_in: int
    refresh_token: str


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _issue_pair(
    db: AsyncSession, svc: TokenService, settings: Settings, user: User
) -> TokenResponse:
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash(raw),
            expires_at=utcnow() + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    await db.commit()
    access, ttl = svc.issue("access", user.id)
    return TokenResponse(access_token=access, expires_in=ttl, refresh_token=raw)


def _aware(dt: datetime) -> datetime:
    # SQLite returns naive datetimes; PostgreSQL returns aware ones.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class Me(BaseModel):
    id: str
    email: str
    plan: str
    store_transcripts: bool


def _me(u: User) -> Me:
    return Me(id=u.id, email=u.email, plan=u.plan, store_transcripts=u.store_transcripts)


@router.post("/auth/register", response_model=Me, status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, db: DB) -> Me:
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Same response shape as validation errors; does not confirm which emails exist
        # beyond what registration inherently reveals.
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered") from None
    return _me(user)


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: DB,
    svc: Annotated[TokenService, Depends(tokens)],
) -> TokenResponse:
    email = body.email.lower()
    client = request.client.host if request.client else "unknown"
    if not _login_limiter.hit(f"{email}|{client}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts, try later")
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not verify_password(body.password, user.password_hash if user else None) or not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")
    return await _issue_pair(db, svc, request.app.state.settings, user)


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: DB,
    svc: Annotated[TokenService, Depends(tokens)],
) -> TokenResponse:
    """Rotate a refresh token. Reusing an already-rotated token revokes all of the user's
    sessions (likely theft)."""
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")
    row = (
        await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == _hash(body.refresh_token))
        )
    ).scalar_one_or_none()
    if row is None:
        raise invalid
    now = utcnow()
    if row.revoked_at is not None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == row.user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.commit()
        raise invalid
    if _aware(row.expires_at) <= now:
        raise invalid
    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise invalid
    row.revoked_at = now
    return await _issue_pair(db, svc, request.app.state.settings, user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: DB) -> None:
    """Revoke a refresh token (idempotent). Access tokens expire on their own (short TTL)."""
    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == _hash(body.refresh_token), RefreshToken.revoked_at.is_(None)
        )
        .values(revoked_at=utcnow())
    )
    await db.commit()


@router.get("/me", response_model=Me)
async def me(user: CurrentUser) -> Me:
    return _me(user)
