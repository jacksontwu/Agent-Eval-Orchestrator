from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import InvalidTokenError, verify_access_token
from app.model.db import get_db
from app.service.auth_service import Principal, dev_principal


def db_session() -> Session:
    yield from get_db()


def require_current_principal(request: Request) -> Principal:
    settings = get_settings()
    if settings.allow_no_auth:
        return dev_principal()
    auth = request.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    if not settings.auth_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AEO_AUTH_SECRET not configured")
    try:
        payload = verify_access_token(token, secret=settings.auth_secret)
    except InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token") from None
    return Principal(
        username=payload.subject,
        source=payload.source,
        groups=payload.groups,
        permissions=payload.permissions,
    )


def require_permission(permission: str) -> Callable[[Principal], Principal]:
    def dependency(principal: Principal = Depends(require_current_principal)) -> Principal:
        if permission not in principal.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing permission: {permission}")
        return principal

    return dependency
