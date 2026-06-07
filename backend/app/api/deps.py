from __future__ import annotations

from fastapi import HTTPException, Query, Request, status
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


def require_token(request: Request, token: str | None = Query(default=None)) -> None:
    settings = get_settings()
    expected = settings.token
    if not expected:
        # Default-deny: refuse to serve protected routes unless a token is configured.
        # Only an explicit dev opt-in (AEO_ALLOW_NO_AUTH=1) leaves the API open.
        if settings.allow_no_auth:
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AEO_TOKEN not configured (set AEO_TOKEN, or AEO_ALLOW_NO_AUTH=1 for local dev)",
        )
    header = request.headers.get("X-AEO-Token")
    cookie = request.cookies.get("aeo_token")
    if token == expected or header == expected or cookie == expected:
        return
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
