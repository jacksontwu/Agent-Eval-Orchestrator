from __future__ import annotations

from fastapi import HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.model.db import get_db


def db_session() -> Session:
    yield from get_db()


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
