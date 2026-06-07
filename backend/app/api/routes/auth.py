from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import db_session, require_current_principal
from app.schema.auth import LoginRequest, PrincipalRead, TokenResponse
from app.service import auth_service

router = APIRouter()


def _principal_read(principal: auth_service.Principal) -> PrincipalRead:
    return PrincipalRead(
        username=principal.username,
        source=principal.source,
        groups=principal.groups,
        permissions=principal.permissions,
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(db_session)) -> TokenResponse:
    principal = auth_service.authenticate(session, body.username, body.password)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid username or password")
    token, expires_at = auth_service.issue_token(principal)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_at=expires_at.isoformat(),
        user=_principal_read(principal),
    )


@router.get("/auth/me", response_model=PrincipalRead)
def me(principal: auth_service.Principal = Depends(require_current_principal)) -> PrincipalRead:
    return _principal_read(principal)
