"""Password hashing (Argon2id) and signed tokens (HS256 JWT)."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings

_hasher = PasswordHasher()  # Argon2id with library-recommended parameters
_DUMMY_HASH = _hasher.hash("timing-equalizer")

TokenType = Literal["access", "stream"]


class TokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time: always runs one Argon2 verification, even for unknown users."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


class TokenService:
    def __init__(self, settings: Settings) -> None:
        secret = settings.jwt_secret.get_secret_value() if settings.jwt_secret else None
        self._key = secret or secrets.token_urlsafe(48)
        self._settings = settings

    def issue(self, typ: TokenType, subject: str, **claims: Any) -> tuple[str, int]:
        ttl = (
            self._settings.access_token_ttl_minutes * 60
            if typ == "access"
            else self._settings.stream_token_ttl_seconds
        )
        now = datetime.now(UTC)
        payload = {
            "sub": subject,
            "typ": typ,
            "iat": now,
            "exp": now + timedelta(seconds=ttl),
            "jti": secrets.token_hex(8),
            **claims,
        }
        return jwt.encode(payload, self._key, algorithm="HS256"), ttl

    def verify(self, token: str, typ: TokenType) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token, self._key, algorithms=["HS256"], options={"require": ["exp", "sub", "typ"]}
            )
        except jwt.PyJWTError as e:
            raise TokenError("invalid or expired token") from e
        if claims.get("typ") != typ:
            raise TokenError("wrong token type")
        return claims
