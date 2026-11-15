from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import TokenError, TokenService
from app.db.models import User
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)

DB = Annotated[AsyncSession, Depends(get_db)]


def tokens(request: Request) -> TokenService:
    svc: TokenService = request.app.state.tokens
    return svc


async def current_user(
    db: DB,
    svc: Annotated[TokenService, Depends(tokens)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise unauthorized
    try:
        claims = svc.verify(creds.credentials, "access")
    except TokenError:
        raise unauthorized from None
    user = await db.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(current_user)]
