from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from jwt import InvalidTokenError as JwtInvalidTokenError
from passlib.context import CryptContext


_password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class InvalidTokenError(ValueError):
    pass


@dataclass(frozen=True)
class TokenPayload:
    subject: str
    source: Literal["config", "db", "dev"]
    groups: list[str]
    permissions: list[str]
    expires_at: datetime


def hash_password(password: str) -> str:
    return _password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_context.verify(password, password_hash)


def create_access_token(
    *,
    subject: str,
    source: Literal["config", "db", "dev"],
    groups: list[str],
    permissions: list[str],
    secret: str,
    ttl_seconds: int,
) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=ttl_seconds)
    payload = {
        "sub": subject,
        "source": source,
        "groups": groups,
        "permissions": permissions,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_access_token(token: str, *, secret: str) -> TokenPayload:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except JwtInvalidTokenError as exc:
        raise InvalidTokenError("invalid access token") from exc

    subject = payload.get("sub")
    source = payload.get("source")
    groups = payload.get("groups")
    permissions = payload.get("permissions")
    exp = payload.get("exp")
    if (
        not isinstance(subject, str)
        or source not in {"config", "db", "dev"}
        or not isinstance(groups, list)
        or not all(isinstance(item, str) for item in groups)
        or not isinstance(permissions, list)
        or not all(isinstance(item, str) for item in permissions)
        or not isinstance(exp, int)
    ):
        raise InvalidTokenError("malformed access token")
    return TokenPayload(
        subject=subject,
        source=source,
        groups=list(groups),
        permissions=list(permissions),
        expires_at=datetime.fromtimestamp(exp, tz=UTC),
    )
